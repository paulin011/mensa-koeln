"""UI smoke test: drives the real app in headless Chromium via CDP.

Needs a running server (python server.py) and chromium. Not part of the
unittest suite — run manually:  python tests/ui_smoke.py [base_url]
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5588"
PORT = 9223

_id = 0


def send(ws, method, params=None):
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id:
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result", {})


def js(ws, expression):
    result = send(ws, "Runtime.evaluate", {"expression": expression, "returnByValue": True})
    if result.get("exceptionDetails"):
        raise RuntimeError(result["exceptionDetails"].get("text") + ": " + expression)
    return result["result"].get("value")


def wait_for(ws, expression, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js(ws, expression):
            return True
        time.sleep(0.2)
    raise TimeoutError(expression)


checks = []


def check(name, condition):
    checks.append((name, bool(condition)))
    print(("  ✓ " if condition else "  ✗ FAIL ") + name)


def main():
    profile = tempfile.mkdtemp(prefix="mensa-ui-test-")
    proc = subprocess.Popen(
        [
            "chromium", "--headless", "--disable-gpu", f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}", "--window-size=900,1400", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        send(ws, "Page.enable")
        send(ws, "Runtime.enable")

        print("1. load #unimensa/2026-06-15")
        send(ws, "Page.navigate", {"url": f"{BASE}/#unimensa/2026-06-15"})
        wait_for(ws, "document.querySelectorAll('.meal-card').length > 0")
        check("meal cards rendered", js(ws, "document.querySelectorAll('#menu .meal-card').length") >= 4)
        check("canteen select follows URL", js(ws, "document.querySelector('#canteen-select').value") == "unimensa")
        check("day chip active", js(ws, "document.querySelector('.day-nav button.active .date').textContent") == "15.06.")
        check("location groups present", js(ws, "document.querySelectorAll('#menu .line-header').length") >= 2)

        print("2. sides sheet")
        check("sides bar visible", js(ws, "!document.querySelector('#sides-bar').hidden"))
        js(ws, "document.querySelector('#sides-button').click()")
        wait_for(ws, "!document.querySelector('#sides-sheet').hidden")
        sides_count = js(ws, "document.querySelectorAll('#sides-body .meal-card').length")
        check("sides sheet lists side dishes", sides_count >= 3)
        js(ws, "document.querySelector('#sides-sheet [data-close]').click()")
        check("sides sheet closes", js(ws, "document.querySelector('#sides-sheet').hidden"))

        print("3. allergen filter + persistence")
        before = js(ws, "document.querySelectorAll('#menu .meal-card').length")
        js(ws, "document.querySelector('#allergen-button').click()")
        wait_for(ws, "!document.querySelector('#allergen-sheet').hidden")
        # exclude gluten (code 11): should also hide 11w sub-codes
        js(ws, """
          [...document.querySelectorAll('#allergen-body .allergen-row')]
            .find(r => r.textContent.trim().startsWith('11 '))
            .querySelector('input').click()
        """)
        after = js(ws, "document.querySelectorAll('#menu .meal-card').length")
        check("excluding gluten hides meals", after < before)
        check(
            "exclusion persisted to localStorage",
            "11" in (js(ws, "localStorage.getItem('mensa.excludedAllergens')") or ""),
        )
        check(
            "no visible meal contains 11/11w",
            js(ws, """
              [...document.querySelectorAll('#menu .meal-allergens li')]
                .every(li => !/^11[a-z]? /.test(li.textContent))
            """),
        )
        js(ws, "document.querySelector('#allergen-sheet [data-close]').click()")
        js(ws, "document.querySelector('#allergen-button').click()")
        js(ws, """
          [...document.querySelectorAll('#allergen-body .allergen-row')]
            .find(r => r.textContent.trim().startsWith('11 '))
            .querySelector('input').click()
        """)
        js(ws, "document.querySelector('#allergen-sheet [data-close]').click()")

        print("4. diet filter")
        js(ws, "document.querySelector('#diet-filters button[data-diet=vegan]').click()")
        check(
            "vegan filter leaves only vegan cards",
            js(ws, """
              [...document.querySelectorAll('#menu .meal-card')]
                .every(c => c.querySelector('.badge.vegan'))
            """),
        )
        js(ws, "document.querySelector('#diet-filters button[data-diet=all]').click()")

        print("5. language toggle")
        js(ws, "document.querySelector('#lang-toggle').click()")
        check("sections in English", js(ws, "document.querySelector('.mealtime-section h2').textContent").endswith("Lunch"))
        check("html lang switched", js(ws, "document.documentElement.lang") == "en")
        js(ws, "document.querySelector('#lang-toggle').click()")

        print("6. two-time meal in both sections (2026-06-10 Chili con carne)")
        js(ws, "location.hash = '#unimensa/2026-06-10'")
        wait_for(ws, "document.querySelector('.day-nav button.active .date')?.textContent === '10.06.'")
        chili = js(ws, """
          [...document.querySelectorAll('#menu .mealtime-section')].map(sec => ({
            title: sec.querySelector('h2').textContent,
            has: [...sec.querySelectorAll('.meal-name')].some(n => n.textContent.includes('Chili con carne')),
          }))
        """)
        check("appears in 2 sections", len([s for s in chili if s["has"]]) == 2)
        check(
            "throughout badge shown",
            js(ws, "[...document.querySelectorAll('.badge.throughout')].length") >= 2,
        )

        print("7. meal rating")
        js(ws, "location.hash = '#unimensa/2026-06-15'")
        wait_for(ws, "document.querySelectorAll('#menu .meal-rating .star').length > 0")
        js(ws, "localStorage.setItem('mensa.client', 'ui-smoke-test-0000')")
        js(ws, "location.reload()")
        wait_for(ws, "document.querySelectorAll('#menu .meal-rating .star').length > 0")
        js(ws, "document.querySelector('#menu .meal-rating .star:nth-child(4)').click()")
        wait_for(ws, "document.querySelectorAll('#menu .meal-rating .star.filled').length === 4")
        check("stars fill after rating", True)
        check(
            "average shown",
            "Ø" in js(ws, "document.querySelector('#menu .meal-rating .rating-avg').textContent"),
        )
        js(ws, "location.reload()")
        wait_for(ws, "document.querySelectorAll('#menu .meal-rating .star').length > 0")
        check(
            "own rating restored after reload",
            js(ws, "document.querySelectorAll('#menu .meal-rating .star.filled').length") == 4,
        )

        print("8. URL routing both ways")
        js(ws, "location.hash = '#robertkoch/2026-06-15'")
        wait_for(ws, "document.querySelector('#canteen-select').value === 'robertkoch'")
        check("hash → canteen select", True)
        js(ws, "document.querySelector('#canteen-select').value = 'spoho'")
        js(ws, "document.querySelector('#canteen-select').dispatchEvent(new Event('change'))")
        check("canteen select → hash", js(ws, "location.hash").startswith("#spoho"))

        ws.close()
    finally:
        proc.terminate()
        proc.wait()
        # remove the test client's vote so it doesn't skew real ratings
        try:
            import sqlite3

            db = os.path.join(os.path.dirname(__file__), "..", "data", "ratings.db")
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM ratings WHERE client_id = 'ui-smoke-test-0000'")
            conn.commit()
            conn.close()
        except Exception:
            pass

    failed = [name for name, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
