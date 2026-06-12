"""Importer for the Köln Mensa (KSTW) CloudMensa API.

Fetches the weekly menu JSON and normalizes it into a clean, grouping-friendly
model that the old OpenMensa structure could not express:

- canteen assignment (``ort_id`` primary, screen locations as fallback)
- diet classification: vegan / vegetarian / fish / meat / unknown
- meal line (cleaned ``menu_type``, e.g. "Querbeet Vegan") and mealtime
  (lunch / dinner, derived from the ABENDESSEN suffix)
- serving sub-location and time window parsed from ``dish_info``
  (e.g. "MG Nord 14.30 - 18.15 Uhr")
- prices per role, allergens with human-readable labels, dish components
"""

import datetime as dt
import re
import string

import requests

WEBSITE_BASE = "https://app.cloudmensa.io/"
DEFAULT_SLUG = "kstw"
SOURCE_URL = "https://www.kstw.de/speiseplan"

# Icons that indicate the kind of meat / animal product in a dish.
MEAT_ICONS = {"G", "R", "S", "L", "W", "NL"}
FISH_ICONS = {"F"}
VEGAN_ICONS = {"VGN"}
VEGETARIAN_ICONS = {"VGT", "V"}

DEFAULT_FOOD_ICON_LABELS = {
    "A": "mit Alkohol",
    "F": "mit Fisch",
    "G": "mit Geflügel",
    "L": "mit Lamm",
    "NL": "Neuland Fleisch",
    "R": "mit Rind",
    "RK": "Rettet die Knolle!",
    "S": "mit Schwein",
    "VGN": "Vegan",
    "VGT": "Vegetarisch",
    "V": "Vegetarisch",
    "W": "mit Wild",
}

# menu_type suffixes that encode pricing tier / time slot, not the line name
LINE_SUFFIXES = {"ST", "SOZIAL", "ABENDESSEN", "1", "2"}

TIME_RANGE_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–]\s*(\d{1,2})[.:](\d{2})")
PAREN_RE = re.compile(r"\(([^)]*)\)")

# Trailing allergen/icon code lists that appear WITHOUT parentheses in some
# names, e.g. "Bami Goreng 11, 13, 16, 21" or "Makkaroni 11,VGN,11w".
# Requires at least two comma-separated tokens so a name that legitimately
# ends in a single number ("Theke 2") is left alone.
_CODE_TOKEN = r"(?:\d{1,2}[a-z]?|VGN|VGT|RK|NL|MLCC|[AVFGRSLW])"
TRAILING_CODES_RE = re.compile(
    rf"[\s,]+({_CODE_TOKEN}(?:\s*,\s*{_CODE_TOKEN})+)\s*$"
)


def strip_trailing_codes(name):
    """Remove a trailing unparenthesized allergen-code list from a name.

    Returns (clean_name, codes). "Bami Goreng 11, 13, 16" -> ("Bami Goreng",
    ["11", "13", "16"]); icon tokens (VGN, ...) are dropped from the codes
    since they carry no allergen info."""
    match = TRAILING_CODES_RE.search(name)
    if not match:
        return name, []
    clean = name[: match.start()].strip(" ,-")
    codes = [
        token.strip()
        for token in match.group(1).split(",")
        if token.strip() and any(ch.isdigit() for ch in token)
    ]
    return clean, codes


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def get_organization_data(slug=DEFAULT_SLUG, timeout=15):
    """Scrape the Supabase URL + publishable API key from the CloudMensa web
    app and resolve the organization metadata (id, name, settings)."""
    base_url = slug if slug.startswith("https://") else f"{WEBSITE_BASE}menu/{slug}"

    response = requests.get(base_url, timeout=timeout)
    response.raise_for_status()
    scripts = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", response.text)

    pattern = r'"(https://[a-zA-Z0-9-]+\.supabase\.co)",\w+="([a-zA-Z0-9\._-]+)"'
    supabase_url = api_key = None
    for script_url in scripts:
        full_url = script_url if script_url.startswith("http") else f"{WEBSITE_BASE}{script_url}"
        js = requests.get(full_url, timeout=timeout)
        js.raise_for_status()
        match = re.search(pattern, js.text)
        if match:
            supabase_url, api_key = match.group(1), match.group(2)
            break

    if not supabase_url or not api_key:
        raise RuntimeError("Could not find Supabase configuration in CloudMensa JavaScript files.")

    headers = {
        "apikey": api_key,
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    rpc = requests.post(
        f"{supabase_url}/rest/v1/rpc/public_get_organization_by_slug",
        headers=headers,
        json={"p_slug": slug},
        timeout=timeout,
    )
    rpc.raise_for_status()
    org = rpc.json()
    if not isinstance(org, dict):
        raise RuntimeError("Organization RPC returned an unexpected payload.")

    return {
        "supabase_url": supabase_url,
        "api_key": api_key,
        "organization_id": org.get("id"),
        "organization_name": org.get("name"),
        "settings": org.get("settings") or {},
    }


def fetch_week_menu(config, start_date, end_date, timeout=30):
    """Fetch the raw menu JSON (list of {date, dishes}) for a date range."""
    headers = {
        "apikey": config["api_key"],
        "authorization": f"Bearer {config['api_key']}",
        "content-type": "application/json",
    }
    payload = {
        "p_organization_id": config["organization_id"],
        "p_start_date": start_date.isoformat(),
        "p_end_date": end_date.isoformat(),
    }
    # p_dedup_fields is the uniqueness key. The site default
    # (name_de, location, ort_id) collapses a meal that is served at both
    # lunch and dinner into a single row; menu_type + dish_info keep the
    # time variants apart so we can merge them ourselves with full info.
    dedup = list(config["settings"].get("public_menu_dedup_custom_fields") or ["name_de", "location", "ort_id", ""])
    for extra in ("menu_type", "dish_info"):
        if extra not in dedup:
            dedup.insert(-1 if dedup and dedup[-1] == "" else len(dedup), extra)
    payload["p_dedup_fields"] = dedup

    response = requests.post(
        f"{config['supabase_url']}/rest/v1/rpc/public_get_week_menu",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def custom_fields_to_dict(custom_fields):
    fields = {}
    for item in custom_fields or []:
        field_id = item.get("field_id")
        if field_id and field_id not in fields:
            fields[field_id] = item.get("value")
    return fields


def _normalize_text(value):
    text = str(value or "").strip().lower()
    return " ".join(text.replace("straße", "strasse").replace("-", " ").split())


def classify_diet(icons):
    s = {icon.upper() for icon in icons}
    if s & VEGAN_ICONS:
        return "vegan"
    if s & VEGETARIAN_ICONS:
        return "vegetarian"
    if s & FISH_ICONS:
        return "fish"
    if s & MEAT_ICONS:
        return "meat"
    return "unknown"


def parse_food_icons(raw):
    return [icon.strip() for icon in str(raw or "").split(",") if icon.strip()]


def parse_line(menu_type):
    """Split menu_type into (line name, mealtime).

    "QUERBEET VEGAN ST"     -> ("Querbeet Vegan", "lunch")
    "WORLDWIDE ABENDESSEN"  -> ("Worldwide", "dinner")
    "xBeilagen"             -> ("Beilagen", "lunch")
    """
    value = str(menu_type or "").strip()
    if value.lower().startswith("x") and len(value) > 1 and value[1].isalpha():
        value = value[1:]

    tokens = value.replace("_", " ").split()
    mealtime = "dinner" if any(t.upper() == "ABENDESSEN" for t in tokens) else "lunch"
    while len(tokens) > 1 and tokens[-1].upper() in LINE_SUFFIXES:
        tokens.pop()
    if len(tokens) == 1 and tokens[0].upper() == "ABENDESSEN":
        tokens = []

    name = string.capwords(" ".join(tokens).lower()) if tokens else None
    return name, mealtime


def parse_serving(dish_info):
    """Parse dish_info into a serving sub-location and time window.

    "MG Nord 14.30 - 18.15 Uhr" -> {"location": "MG Nord", "start": "14:30", "end": "18:15"}
    Values that are just counter numbers or icon codes ("1", "2", "VGT", "F")
    carry no serving info and yield None.
    """
    raw = str(dish_info or "").strip()
    if not raw or raw.isdigit() or raw.upper() in {"VGT", "VGN", "F", "G", "R", "S"}:
        return None

    match = TIME_RANGE_RE.search(raw)
    start = end = None
    location = raw
    if match:
        start = f"{int(match.group(1)):02d}:{match.group(2)}"
        end = f"{int(match.group(3)):02d}:{match.group(4)}"
        location = (raw[: match.start()] + " " + raw[match.end():]).replace("Uhr", "")
        # "MG Nord & MG Nord" appears in the source data; dedupe the parts
        parts = []
        for part in location.split("&"):
            part = " ".join(part.split()).strip(" -–,")
            if part and part not in parts:
                parts.append(part)
        location = " & ".join(parts)

    return {"location": location or None, "start": start, "end": end, "raw": raw}


def _parse_price(value):
    if value is None:
        return None
    text = str(value).strip().replace("€", "").replace(",", ".")
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def extract_prices(dish, fields):
    prices = {
        "student": _parse_price(dish.get("price")) or _parse_price(fields.get("price_1")),
        "employee": _parse_price(fields.get("price_2")),
        "guest": _parse_price(fields.get("price_3")),
        "external": _parse_price(fields.get("price_4")),
    }
    prices = {role: value for role, value in prices.items() if value is not None}

    per_100g = str(fields.get("preis_gramm") or "").strip()
    unit = "100g" if per_100g and per_100g != "0" else None
    return prices, unit


def parse_allergen_legend(raw, legend):
    """allergens_names is a string like "11w=Enthält Weizen Gluten | wheat, ...".
    Merge code -> {de, en} labels into `legend`."""
    for part in str(raw or "").split(","):
        if "=" not in part:
            continue
        code, label = part.split("=", 1)
        de, _, en = label.partition("|")
        code = code.strip()
        if code and code not in legend:
            legend[code] = {"de": de.strip(), "en": en.strip() or None}


def split_components(fields, lang_suffixes):
    """dish_ger_1..5 hold the dish components (main + sides). Returns a list of
    {name, allergens} with allergen codes stripped from the names."""
    components = []
    for field_id in lang_suffixes:
        raw = str(fields.get(field_id) or "").strip()
        if not raw:
            continue
        codes = []
        for group in PAREN_RE.findall(raw):
            codes.extend(c.strip() for c in group.split(",") if c.strip())
        name = " ".join(PAREN_RE.sub("", raw).split()).strip(" ,")
        name, trailing = strip_trailing_codes(name)
        codes.extend(trailing)
        if name:
            components.append({"name": name, "allergens": codes})
    return components


def _canteen_screen_set(canteen):
    return {_normalize_text(x) for x in (canteen.get("screen_locations") or []) if x}


def _dish_screen_set(dish, fields):
    screens = set()
    for screen in dish.get("screens") or []:
        if screen.get("location"):
            screens.add(_normalize_text(screen["location"]))
    if fields.get("location"):
        screens.add(_normalize_text(fields["location"]))
    return screens


def match_canteen(dish, fields, canteens):
    """Return the canteen id a dish belongs to (ort_id authoritative,
    screen locations as fallback), or None."""
    dish_ort = str(fields.get("ort_id") or "").strip()
    if dish_ort:
        for cid, canteen in canteens.items():
            if str(canteen.get("ort_id") or "").strip() == dish_ort:
                return cid
        return None

    dish_screens = _dish_screen_set(dish, fields)
    if not dish_screens:
        return None
    for cid, canteen in canteens.items():
        if _canteen_screen_set(canteen) & dish_screens:
            return cid
    return None


# ---------------------------------------------------------------------------
# Main normalization
# ---------------------------------------------------------------------------

def food_icon_labels_from_settings(settings):
    labels = dict(DEFAULT_FOOD_ICON_LABELS)
    for icon in settings.get("public_menu_food_icon_legend", []):
        icon_id = str(icon.get("icon_id") or "").strip()
        label = str(icon.get("label_de") or icon.get("label_en") or "").strip()
        if icon_id and label:
            labels[icon_id] = label
    return labels


def normalize_menu(menu_json, canteens, icon_labels):
    """Turn the raw CloudMensa response into the app model.

    Returns {"days": [...], "menu": {canteen_id: {date: [meal, ...]}},
             "allergen_legend": {code: {de, en}}}.
    """
    legend = {}
    for day in menu_json:
        for dish in day.get("dishes", []):
            fields = custom_fields_to_dict(dish.get("custom_fields"))
            parse_allergen_legend(fields.get("allergens_names"), legend)

    menu = {cid: {} for cid in canteens}
    days = []

    for day in menu_json:
        date = str(day.get("date") or "").strip()
        if not date:
            continue
        days.append(date)

        for dish in day.get("dishes", []):
            fields = custom_fields_to_dict(dish.get("custom_fields"))
            canteen_id = match_canteen(dish, fields, canteens)
            if canteen_id is None:
                continue

            meal = build_meal(dish, fields, legend, icon_labels)
            if meal is None:
                continue
            menu[canteen_id].setdefault(date, []).append(meal)

    for canteen_days in menu.values():
        for date, meals in canteen_days.items():
            canteen_days[date] = merge_duplicate_meals(meals)
            canteen_days[date].sort(key=_meal_sort_key)

    return {"days": sorted(set(days)), "menu": menu, "allergen_legend": legend}


# Placeholder entries in the source data that are not actual dishes.
JUNK_NAMES = {"tagesrestproduktion"}

# Generic side components: a bare "Salat" + "Dessert" in a main meal means
# "two sides of your choice" (they are interchangeable at the counter), and
# some dishes spell it out as "2 Beilagen" / "3 Beilagen" directly.
GENERIC_SIDE_NAMES = {"salat", "dessert", "salad"}
SIDES_COUNT_RE = re.compile(r"^(\d+)\s*(?:beilagen?|sides?(?:\s+dishes?)?|side\s+dishes?)$", re.IGNORECASE)


def extract_choice_sides(components):
    """Split side components into concrete ones and a choice-sides count.

    [{Pommes Wedges}, {Salat}, {Dessert}] -> ([{Pommes Wedges}], 2)
    [{3 Beilagen}]                        -> ([], 3)
    """
    fixed = []
    count = 0
    for component in components:
        name = component["name"].strip()
        match = SIDES_COUNT_RE.match(name)
        if match:
            count += int(match.group(1))
        elif _normalize_text(name) in GENERIC_SIDE_NAMES:
            count += 1
        else:
            fixed.append(component)
    return fixed, count


def _meal_sort_key(meal):
    """Order: mealtime, then serving time, then serving location, then name.

    Meals without serving info sort after located ones within their mealtime."""
    serving = meal.get("serving") or {}
    return (
        0 if meal["mealtime"] == "lunch" else 1,
        serving.get("start") or "99:99",
        serving.get("location") or "￿",
        meal.get("name") or "",
    )


def merge_duplicate_meals(meals):
    """Merge instances of the same meal that appear once per serving slot
    (e.g. lunch at one counter, dinner at another) into a single meal with a
    `servings` list. A meal available at both lunch and dinner gets
    `throughout: true`; its section/sort slot follows its earliest serving."""
    merged = {}
    order = []
    for meal in meals:
        key = (
            _normalize_text(meal["name"]),
            tuple(_normalize_text(c["name"]) for c in meal["components"]),
            meal["diet"],
            tuple(sorted(meal["prices"].items())),
            meal["price_unit"],
        )
        if key not in merged:
            merged[key] = meal
            order.append(meal)
            continue
        target = merged[key]
        target["_mealtimes"].update(meal["_mealtimes"])
        for serving in meal["servings"]:
            if serving not in target["servings"]:
                target["servings"].append(serving)

    for meal in order:
        meal["servings"].sort(key=lambda s: (s["start"] or "99:99", s["location"] or ""))
        meal["serving"] = meal["servings"][0] if meal["servings"] else None
        mealtimes = meal.pop("_mealtimes")
        mealtimes.update(s["mealtime"] for s in meal["servings"])
        meal["mealtimes"] = [mt for mt in ("lunch", "dinner") if mt in mealtimes]
        meal["throughout"] = len(meal["mealtimes"]) > 1
        if meal["serving"]:
            meal["mealtime"] = meal["serving"]["mealtime"]
        else:
            meal["mealtime"] = meal["mealtimes"][0]
    return order


def build_meal(dish, fields, legend, icon_labels):
    components = split_components(
        fields, ["dish_ger_1", "dish_ger_2", "dish_ger_3", "dish_ger_4", "dish_ger_5"]
    )
    components_en = split_components(
        fields, ["dish_1_eng", "dish_2_eng", "dish_3_eng", "dish_4_eng", "dish_5_eng"]
    )

    raw_name = str(dish.get("name_de") or "").strip()
    fallback_codes = []
    if components:
        name = components[0]["name"]
    else:
        name, fallback_codes = strip_trailing_codes(
            " ".join(PAREN_RE.sub("", raw_name).split())
        )
        paren_codes = [
            c.strip()
            for group in PAREN_RE.findall(raw_name)
            for c in group.split(",")
            if c.strip()
        ]
        fallback_codes = paren_codes + fallback_codes
    if not name or _normalize_text(name) in JUNK_NAMES:
        return None

    sides, choice_sides = extract_choice_sides(components[1:])
    sides_en, _ = extract_choice_sides(components_en[1:])

    icons = parse_food_icons(fields.get("food_icon"))
    line, mealtime = parse_line(fields.get("menu_type"))
    serving = parse_serving(fields.get("dish_info"))
    prices, price_unit = extract_prices(dish, fields)

    # Side dishes: the "Beilagen" line, or line-less bare items at a low
    # fixed price (per-100g bistro items are mains sold by weight).
    is_side = line == "Beilagen" or (
        line is None
        and not sides
        and not choice_sides
        and price_unit is None
        and prices.get("student") is not None
        and prices.get("student") <= 1.5
    )

    allergen_codes = [
        code.strip()
        for code in str(fields.get("allergens_numbers") or "").split(",")
        if code.strip()
    ]
    if not allergen_codes:
        seen = set()
        for codes in [c["allergens"] for c in components] + [fallback_codes]:
            for code in codes:
                if code not in seen:
                    seen.add(code)
                    allergen_codes.append(code)

    allergens = [
        {"code": code, "label": (legend.get(code) or {}).get("de") or code}
        for code in allergen_codes
    ]

    return {
        "id": dish.get("id"),
        "name": name,
        "name_en": (components_en[0]["name"] if components_en else None)
        or (str(dish.get("name_en") or "").strip() or None),
        "components": sides,
        "components_en": sides_en,
        "choice_sides": choice_sides,
        "description": str(dish.get("description_de") or "").strip() or None,
        "line": line,
        "mealtime": mealtime,
        "is_side": is_side,
        "serving": serving,
        "servings": [dict(serving, mealtime=mealtime)] if serving else [],
        "_mealtimes": {mealtime},
        "diet": classify_diet(icons),
        "icons": [
            {"code": icon, "label": icon_labels.get(icon.upper(), icon)} for icon in icons
        ],
        "prices": prices,
        "price_unit": price_unit,
        "allergens": allergens,
        "image_url": dish.get("image_url"),
    }


def import_plan(canteens, config=None, start_date=None, days=14):
    """High-level entry point: fetch + normalize a date range starting at the
    Monday of the current week (so 'earlier this week' stays visible)."""
    if config is None:
        config = get_organization_data()
    icon_labels = food_icon_labels_from_settings(config["settings"])

    today = dt.date.today()
    if start_date is None:
        start_date = today - dt.timedelta(days=today.weekday())
    end_date = start_date + dt.timedelta(days=days - 1)

    raw = fetch_week_menu(config, start_date, end_date)
    result = normalize_menu(raw, canteens, icon_labels)
    result["organization"] = config.get("organization_name")
    result["source"] = SOURCE_URL
    result["range"] = {"start": start_date.isoformat(), "end": end_date.isoformat()}
    result["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return result
