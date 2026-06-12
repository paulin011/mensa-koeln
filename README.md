# Mensa Köln — API & Web App

A fresh importer for the KSTW (Kölner Studierendenwerk) CloudMensa API plus a
Flask server and PWA-ready web app for browsing the mensa plan. Unlike the
OpenMensa feed structure, this model understands:

- **Diet status** — vegan / vegetarian / fish / meat, derived from the
  `food_icon` field (`VGN`, `VGT`/`V`, `F`, `G`/`R`/`S`/`L`/`W`/`NL`)
- **Mealtimes** — lunch vs. dinner (`ABENDESSEN` suffix in `menu_type`)
- **Serving locations & times** — parsed from `dish_info`
  (`"MG Nord 14.30 - 18.15 Uhr"` → location `MG Nord`, 14:30–18:15)
- **Prices per role** — student / employee / guest / external, plus
  per-100g pricing (`preis_gramm`)
- **Allergens** — codes with human-readable labels from the week-wide
  legend; trailing unparenthesized code lists in dish names
  ("Bami Goreng 11, 13, 16") are stripped and converted into allergens
- **Components** — main dish + sides split out (`dish_ger_1..5`),
  German and English
- **Choice sides** — generic "Salat" / "Dessert" components and explicit
  "2/3 Beilagen" entries mean interchangeable sides of your choice; they are
  collapsed into a `choice_sides` count
- **Side dishes** — items from the "Beilagen" line (and line-less bare items
  at side-dish prices) get `is_side: true` and live in a bottom sheet in the
  UI instead of cluttering the plan

## Data quirks handled

- Meals are grouped/ordered by **mealtime → serving time → serving
  location**. The `menu_type` line names ("MEISTERWERK", "QUERBEET", …) carry
  no useful meaning for diners; they are kept as a `line` field but not used
  for grouping.
- Placeholder dishes named "Tagesrestproduktion" are discarded.
- During lecture time some meals are served at both lunch and dinner. The
  upstream API's default dedup key collapses these to a single row, so the
  importer requests a richer key (`menu_type` + `dish_info` added to
  `p_dedup_fields`) and merges the instances itself: one meal with a
  `servings` list, `mealtimes: ["lunch", "dinner"]` and `throughout: true`.
  Such meals are shown in **both** mealtime sections. Instances only merge
  when name, components, diet and prices all match — same-named offerings
  with different prices (side portion vs. full dish) stay separate.

## Web app

Vanilla JS single-page app, no build step:

- URL routing: `/#<canteen>/<date>` (e.g. `/#unimensa/2026-06-15`) — easy to
  share and test
- Day navigation, canteen picker, vegetarian/vegan filters, price-role
  selector — all persisted in localStorage
- **Allergen exclusion filter** (⚠️ button): hides meals containing checked
  allergens, including sub-codes (excluding `11` also hides `11w`); persisted
- Side dishes in a bottom sheet (🥗 button)
- Serving groups whose time window has passed today are greyed out
- German/English toggle (uses the API's English fields where present)
- PWA: installable, last loaded plan readable offline (service worker)
- Light/dark theme via `prefers-color-scheme`

## Running

```sh
pip install -r requirements.txt   # flask + requests
python server.py                  # http://127.0.0.1:5588
```

No API key configuration needed: the Supabase publishable key is scraped from
the CloudMensa web app at startup (cached 12 h). The menu itself is cached in
memory for 30 min. Env vars: `MENSA_PORT`, `MENSA_HOST`, `MENSA_PLAN_TTL`.

### Deployment

```sh
docker build -t mensa-koeln .
docker run -d -p 8000:8000 --restart unless-stopped mensa-koeln
```

Or without Docker: `pip install -r requirements.txt gunicorn` and
`gunicorn --bind 0.0.0.0:8000 --workers 2 server:app` (behind a reverse proxy
for TLS).

## API

| Endpoint | Description |
|---|---|
| `GET /api/canteens` | Canteen metadata (address, hours, coordinates, meal count) |
| `GET /api/plan` | Full normalized plan for all canteens, flat meal lists |
| `GET /api/plan/<id>` | One canteen; per day `{mealtimes: [...], sides: [...]}`, meals grouped by serving spot |
| `GET /api/refresh` | Force a re-fetch from the upstream API |

Meal model:

```json
{
  "name": "Chili con carne",
  "name_en": "Chili con carne",
  "components": [{"name": "Reis", "allergens": []}],
  "choice_sides": 2,
  "line": "Worldwide",
  "mealtime": "lunch",
  "mealtimes": ["lunch", "dinner"],
  "throughout": true,
  "is_side": false,
  "serving": {"location": "MG Nord", "start": "11:30", "end": "14:15", "mealtime": "lunch"},
  "servings": [{...}, {"location": "MG Nord", "start": "14:30", "end": "18:15", "mealtime": "dinner"}],
  "diet": "meat",
  "icons": [{"code": "R", "label": "mit Rind"}],
  "prices": {"student": 3.3, "employee": 5.6, "guest": 7.0},
  "price_unit": null,
  "allergens": [{"code": "11w", "label": "Enthält Weizen Gluten"}]
}
```

## Tests

```sh
python -m unittest discover -s tests     # 44 unit tests, no network needed
python tests/ui_smoke.py                 # drives the real UI in headless
                                         # Chromium via CDP (needs a running
                                         # server + chromium)
```

## Files

- `importer.py` — fetch + normalize (standalone)
- `server.py` — Flask API + static serving, in-memory caching with
  stale-data fallback when upstream is down
- `data/canteens.json` — canteen metadata incl. `ort_id` and screen locations
  used for dish→canteen matching (ort_id authoritative, screens as fallback)
- `static/` — the web app (incl. `manifest.json`, `sw.js`)
- `tests/` — unit tests + CDP UI smoke test
