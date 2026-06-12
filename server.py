"""Flask server for the Köln Mensa plan.

Serves a JSON API on top of the CloudMensa importer plus the static web app.

Endpoints:
    GET /api/canteens          canteen metadata (id, name, address, hours, ...)
    GET /api/plan              full normalized plan for all canteens
    GET /api/plan/<canteen>    plan for one canteen, meals grouped per day by
                               mealtime and line
    GET /api/refresh           force re-fetch from the upstream API

The upstream data is cached in memory (default 30 min TTL); the organization
config (scraped API key) is cached for 12 h.
"""

import json
import logging
import os
import threading
import time

from flask import Flask, abort, jsonify, request, send_from_directory

import importer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

PLAN_TTL = int(os.environ.get("MENSA_PLAN_TTL", "1800"))
CONFIG_TTL = int(os.environ.get("MENSA_CONFIG_TTL", str(12 * 3600)))

with open(os.path.join(APP_DIR, "data", "canteens.json"), encoding="utf8") as f:
    CANTEENS = json.load(f)

app = Flask(__name__, static_folder=None)
log = logging.getLogger("mensa")

_lock = threading.Lock()
_cache = {"plan": None, "plan_time": 0.0, "config": None, "config_time": 0.0}


def _get_config():
    now = time.time()
    if _cache["config"] is None or now - _cache["config_time"] > CONFIG_TTL:
        _cache["config"] = importer.get_organization_data()
        _cache["config_time"] = now
    return _cache["config"]


def get_plan(force=False):
    with _lock:
        now = time.time()
        fresh = _cache["plan"] is not None and now - _cache["plan_time"] <= PLAN_TTL
        if fresh and not force:
            return _cache["plan"]
        try:
            plan = importer.import_plan(CANTEENS, config=_get_config())
        except Exception:
            # Serve stale data if the upstream API is temporarily down.
            if _cache["plan"] is not None:
                log.exception("Plan refresh failed, serving stale data")
                return _cache["plan"]
            raise
        _cache["plan"] = plan
        _cache["plan_time"] = now
        return plan


def canteen_meta(cid):
    c = CANTEENS[cid]
    return {
        "id": cid,
        "name": c["name"],
        "address": f"{c['strasse'].strip()}, {c['plz']} {c['ort']}",
        "city": c["ort"],
        "latitude": c["latitude"],
        "longitude": c["longitude"],
        "phone": c["phone"],
        "hours": c["infokurz"],
    }


def group_meals(meals):
    """Group a flat day list into {"mealtimes": [...], "sides": [...]}.

    A meal served at both lunch and dinner appears in BOTH mealtime blocks
    (under the serving spot of that time). Side dishes are split out into a
    flat `sides` list. Groups are ordered by serving time, then location;
    meals without serving info land in a trailing group."""
    sides = [m for m in meals if m.get("is_side")]
    mains = [m for m in meals if not m.get("is_side")]

    by_time = {}
    for meal in mains:
        placements = {}
        for serving in meal["servings"]:
            placements.setdefault(serving["mealtime"], serving)
        for mt in meal.get("mealtimes") or [meal["mealtime"]]:
            placements.setdefault(mt, None)

        for mt, serving in placements.items():
            block = by_time.setdefault(mt, {"mealtime": mt, "_index": {}})
            serving = serving or {}
            key = (serving.get("location"), serving.get("start"), serving.get("end"))
            group = block["_index"].setdefault(
                key,
                {"location": key[0], "start": key[1], "end": key[2], "meals": []},
            )
            group["meals"].append(meal)

    mealtimes = []
    for mt in ("lunch", "dinner"):
        block = by_time.get(mt)
        if not block:
            continue
        groups = sorted(
            block["_index"].values(),
            key=lambda g: (g["start"] or "99:99", g["location"] or "￿"),
        )
        mealtimes.append({"mealtime": mt, "groups": groups})

    return {"mealtimes": mealtimes, "sides": sides}


@app.get("/api/canteens")
def api_canteens():
    plan = get_plan()
    out = []
    for cid in CANTEENS:
        meta = canteen_meta(cid)
        meta["meal_count"] = sum(len(m) for m in plan["menu"].get(cid, {}).values())
        out.append(meta)
    return jsonify(out)


@app.get("/api/plan")
def api_plan():
    plan = get_plan()
    return jsonify(
        {
            "organization": plan["organization"],
            "source": plan["source"],
            "range": plan["range"],
            "fetched_at": plan["fetched_at"],
            "days": plan["days"],
            "allergen_legend": plan["allergen_legend"],
            "canteens": {cid: canteen_meta(cid) for cid in CANTEENS},
            "menu": plan["menu"],
        }
    )


@app.get("/api/plan/<cid>")
def api_plan_canteen(cid):
    if cid not in CANTEENS:
        abort(404, description="Unknown canteen")
    plan = get_plan()
    days = {
        date: group_meals(meals)
        for date, meals in plan["menu"].get(cid, {}).items()
    }
    return jsonify(
        {
            "canteen": canteen_meta(cid),
            "range": plan["range"],
            "fetched_at": plan["fetched_at"],
            "allergen_legend": plan["allergen_legend"],
            "days": days,
        }
    )


@app.get("/api/refresh")
def api_refresh():
    plan = get_plan(force=True)
    return jsonify({"fetched_at": plan["fetched_at"], "days": plan["days"]})


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("MENSA_PORT", "5588"))
    host = os.environ.get("MENSA_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
