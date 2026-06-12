"""Tests for the server's grouping logic and API endpoints (with a stubbed
importer so no network access is needed)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from tests.test_importer import make_dish
import importer


def build_meals(*dishes):
    canteens = {"unimensa": {"ort_id": "201", "screen_locations": []}}
    menu_json = [{"date": "2026-06-15", "dishes": list(dishes)}]
    result = importer.normalize_menu(menu_json, canteens, importer.DEFAULT_FOOD_ICON_LABELS)
    return result["menu"]["unimensa"]["2026-06-15"]


class GroupMealsTest(unittest.TestCase):
    def test_two_time_meal_in_both_blocks(self):
        meals = build_meals(
            make_dish(menu_type="QUERBEET ST", dish_info="MG Nord 11.30 - 14.15 Uhr"),
            make_dish(menu_type="QUERBEET ABENDESSEN", dish_info="MG Nord 14.30 - 18.15 Uhr"),
        )
        grouped = server.group_meals(meals)
        blocks = {b["mealtime"]: b for b in grouped["mealtimes"]}
        self.assertIn("lunch", blocks)
        self.assertIn("dinner", blocks)
        lunch_names = [m["name"] for g in blocks["lunch"]["groups"] for m in g["meals"]]
        dinner_names = [m["name"] for g in blocks["dinner"]["groups"] for m in g["meals"]]
        self.assertIn("Testgericht", lunch_names)
        self.assertIn("Testgericht", dinner_names)
        # placed under the serving spot of the respective time
        self.assertEqual(blocks["lunch"]["groups"][0]["start"], "11:30")
        self.assertEqual(blocks["dinner"]["groups"][0]["start"], "14:30")

    def test_sides_split_out(self):
        meals = build_meals(
            make_dish(),
            make_dish(name="Reis", menu_type="xBeilagen", components=["Reis"], price=0.65),
        )
        grouped = server.group_meals(meals)
        side_names = [m["name"] for m in grouped["sides"]]
        self.assertEqual(side_names, ["Reis"])
        main_names = [
            m["name"]
            for b in grouped["mealtimes"]
            for g in b["groups"]
            for m in g["meals"]
        ]
        self.assertNotIn("Reis", main_names)

    def test_groups_ordered_by_time_then_location(self):
        meals = build_meals(
            make_dish(name="Spät", dish_info="MG Nord 13.00 - 14.00 Uhr"),
            make_dish(name="Früh", dish_info="EG Süd 11.00 - 12.00 Uhr"),
            make_dish(name="Ohne", dish_info=""),
        )
        grouped = server.group_meals(meals)
        lunch = grouped["mealtimes"][0]
        starts = [g["start"] for g in lunch["groups"]]
        self.assertEqual(starts, ["11:00", "13:00", None])


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meals_menu = {
            "unimensa": {
                "2026-06-15": build_meals(
                    make_dish(),
                    make_dish(name="Reis", menu_type="xBeilagen", components=["Reis"], price=0.65),
                )
            }
        }
        cls.fake_plan = {
            "organization": "Test",
            "source": "https://example.org",
            "range": {"start": "2026-06-15", "end": "2026-06-19"},
            "fetched_at": "2026-06-15T00:00:00+00:00",
            "days": ["2026-06-15"],
            "allergen_legend": {},
            "menu": meals_menu,
        }

    def setUp(self):
        self.patcher = mock.patch.object(server, "get_plan", return_value=self.fake_plan)
        self.patcher.start()
        self.client = server.app.test_client()

    def tearDown(self):
        self.patcher.stop()

    def test_plan_endpoint(self):
        response = self.client.get("/api/plan")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("unimensa", data["menu"])
        self.assertIn("unimensa", data["canteens"])

    def test_canteen_plan_grouped(self):
        response = self.client.get("/api/plan/unimensa")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        day = data["days"]["2026-06-15"]
        self.assertIn("mealtimes", day)
        self.assertIn("sides", day)
        self.assertEqual(len(day["sides"]), 1)

    def test_unknown_canteen_404(self):
        response = self.client.get("/api/plan/nope")
        self.assertEqual(response.status_code, 404)

    def test_canteens_endpoint(self):
        response = self.client.get("/api/canteens")
        self.assertEqual(response.status_code, 200)
        ids = [c["id"] for c in response.get_json()]
        self.assertIn("unimensa", ids)

    def test_static_index(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mensa", response.data)


class RatingsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meals = build_meals(make_dish())
        cls.key = meals[0]["rating_key"]
        cls.fake_plan = {
            "organization": "Test",
            "source": "https://example.org",
            "range": {"start": "2026-06-15", "end": "2026-06-19"},
            "fetched_at": "2026-06-15T00:00:00+00:00",
            "days": ["2026-06-15"],
            "allergen_legend": {},
            "menu": {"unimensa": {"2026-06-15": meals}},
        }

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.patchers = [
            mock.patch.object(server, "get_plan", return_value=self.fake_plan),
            mock.patch.object(server, "DB_PATH", self.db_path),
        ]
        for p in self.patchers:
            p.start()
        self.client = server.app.test_client()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        os.unlink(self.db_path)

    def rate(self, **overrides):
        payload = {"key": self.key, "stars": 4, "client": "client-aaaa-0001"}
        payload.update(overrides)
        return self.client.post("/api/rate", json=payload)

    def test_rate_and_aggregate(self):
        response = self.rate(stars=4)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"avg": 4.0, "count": 1, "mine": 4})

        self.rate(stars=2, client="client-bbbb-0002")
        data = self.client.get("/api/ratings").get_json()
        self.assertEqual(data[self.key], {"avg": 3.0, "count": 2})

    def test_re_rating_updates_not_duplicates(self):
        self.rate(stars=5)
        self.rate(stars=1)
        data = self.client.get("/api/ratings").get_json()
        self.assertEqual(data[self.key], {"avg": 1.0, "count": 1})

    def test_own_rating_included_for_client(self):
        self.rate(stars=3)
        data = self.client.get("/api/ratings?client=client-aaaa-0001").get_json()
        self.assertEqual(data[self.key]["mine"], 3)
        anon = self.client.get("/api/ratings").get_json()
        self.assertNotIn("mine", anon[self.key])

    def test_validation(self):
        self.assertEqual(self.rate(stars=0).status_code, 400)
        self.assertEqual(self.rate(stars=6).status_code, 400)
        self.assertEqual(self.rate(stars="4").status_code, 400)
        self.assertEqual(self.rate(client="x").status_code, 400)
        self.assertEqual(self.rate(key="DROP TABLE").status_code, 400)
        self.assertEqual(self.rate(key="not-a-known-meal").status_code, 400)

    def test_meal_model_has_rating_key(self):
        self.assertTrue(self.key)
        self.assertRegex(self.key, r"^[a-z0-9-]+$")


if __name__ == "__main__":
    unittest.main()
