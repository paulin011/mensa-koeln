"""Unit tests for the importer's parsing and normalization logic."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importer


class StripTrailingCodesTest(unittest.TestCase):
    def test_plain_number_list(self):
        name, codes = importer.strip_trailing_codes("Bami Goreng 11, 13, 16, 21")
        self.assertEqual(name, "Bami Goreng")
        self.assertEqual(codes, ["11", "13", "16", "21"])

    def test_mixed_icons_and_codes(self):
        name, codes = importer.strip_trailing_codes("Makkaroni mit Gemüse - Mediterran 11,VGN,11w")
        self.assertEqual(name, "Makkaroni mit Gemüse - Mediterran")
        self.assertEqual(codes, ["11", "11w"])  # VGN is an icon, not an allergen

    def test_single_trailing_number_is_kept(self):
        # could be a legitimate name ("Theke 2"); only strip lists
        name, codes = importer.strip_trailing_codes("Pizza 2")
        self.assertEqual(name, "Pizza 2")
        self.assertEqual(codes, [])

    def test_clean_name_untouched(self):
        name, codes = importer.strip_trailing_codes("Hähnchen-Crossies")
        self.assertEqual(name, "Hähnchen-Crossies")
        self.assertEqual(codes, [])


class ParseServingTest(unittest.TestCase):
    def test_location_and_time(self):
        s = importer.parse_serving("MG Nord 14.30 - 18.15 Uhr")
        self.assertEqual(s["location"], "MG Nord")
        self.assertEqual(s["start"], "14:30")
        self.assertEqual(s["end"], "18:15")

    def test_duplicate_locations_deduped(self):
        s = importer.parse_serving("MG Nord & MG Nord  11.30 - 14.30 Uhr")
        self.assertEqual(s["location"], "MG Nord")

    def test_distinct_locations_joined(self):
        s = importer.parse_serving("EG Süd & EG Nord 11:30 - 14:30 Uhr")
        self.assertEqual(s["location"], "EG Süd & EG Nord")

    def test_colon_times_with_suffix(self):
        s = importer.parse_serving("EG Süd - 11:30 - 14:30 Uhr Ampelcounter")
        self.assertEqual(s["start"], "11:30")
        self.assertIn("Ampelcounter", s["location"])

    def test_location_only(self):
        s = importer.parse_serving("MG Süd Restaurant 2")
        self.assertEqual(s["location"], "MG Süd Restaurant 2")
        self.assertIsNone(s["start"])

    def test_junk_values(self):
        for junk in ("", "1", "2", "VGT", "F", None):
            self.assertIsNone(importer.parse_serving(junk), junk)


class ParseLineTest(unittest.TestCase):
    def test_suffix_stripping(self):
        self.assertEqual(importer.parse_line("QUERBEET VEGAN ST"), ("Querbeet Vegan", "lunch"))
        self.assertEqual(importer.parse_line("QUERBEET SOZIAL"), ("Querbeet", "lunch"))
        self.assertEqual(importer.parse_line("MEISTERWERK 2"), ("Meisterwerk", "lunch"))

    def test_dinner_detection(self):
        self.assertEqual(importer.parse_line("WORLDWIDE ABENDESSEN"), ("Worldwide", "dinner"))

    def test_x_prefix(self):
        self.assertEqual(importer.parse_line("xBeilagen"), ("Beilagen", "lunch"))

    def test_empty(self):
        self.assertEqual(importer.parse_line(""), (None, "lunch"))
        self.assertEqual(importer.parse_line(None), (None, "lunch"))


class ClassifyDietTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(importer.classify_diet(["VGN"]), "vegan")
        self.assertEqual(importer.classify_diet(["VGT"]), "vegetarian")
        self.assertEqual(importer.classify_diet(["v"]), "vegetarian")
        self.assertEqual(importer.classify_diet(["F"]), "fish")
        self.assertEqual(importer.classify_diet(["G"]), "meat")
        self.assertEqual(importer.classify_diet(["S", "RK"]), "meat")
        self.assertEqual(importer.classify_diet(["RK"]), "unknown")
        self.assertEqual(importer.classify_diet([]), "unknown")

    def test_vegan_wins_over_meat_icons(self):
        self.assertEqual(importer.classify_diet(["VGN", "G"]), "vegan")


class ChoiceSidesTest(unittest.TestCase):
    def test_generic_salat_dessert(self):
        comps = [
            {"name": "Pommes Wedges", "allergens": []},
            {"name": "Salat", "allergens": []},
            {"name": "Dessert", "allergens": ["1"]},
        ]
        fixed, count = importer.extract_choice_sides(comps)
        self.assertEqual([c["name"] for c in fixed], ["Pommes Wedges"])
        self.assertEqual(count, 2)

    def test_explicit_count(self):
        fixed, count = importer.extract_choice_sides([{"name": "3 Beilagen", "allergens": []}])
        self.assertEqual(fixed, [])
        self.assertEqual(count, 3)

    def test_real_salad_dish_untouched(self):
        fixed, count = importer.extract_choice_sides([{"name": "Bunter Krautsalat", "allergens": []}])
        self.assertEqual(len(fixed), 1)
        self.assertEqual(count, 0)


def make_dish(
    name="Testgericht",
    ort_id="201",
    menu_type="QUERBEET ST",
    dish_info="EG Nord 11.30 - 14.30 Uhr",
    food_icon="VGN",
    price=3.3,
    components=None,
    name_de=None,
    allergens_numbers="11, 17",
):
    fields = [
        {"field_id": "ort_id", "value": ort_id},
        {"field_id": "menu_type", "value": menu_type},
        {"field_id": "dish_info", "value": dish_info},
        {"field_id": "food_icon", "value": food_icon},
        {"field_id": "allergens_numbers", "value": allergens_numbers},
        {"field_id": "price_2", "value": "5.60"},
        {"field_id": "price_3", "value": "7.00"},
    ]
    comps = components if components is not None else [name, "Salat", "Dessert"]
    for i, comp in enumerate(comps, 1):
        fields.append({"field_id": f"dish_ger_{i}", "value": comp})
    return {
        "id": f"id-{name}-{menu_type}",
        "name_de": name_de or name,
        "price": price,
        "custom_fields": fields,
        "screens": [],
    }


class BuildMealTest(unittest.TestCase):
    def build(self, dish):
        fields = importer.custom_fields_to_dict(dish.get("custom_fields"))
        return importer.build_meal(dish, fields, {}, importer.DEFAULT_FOOD_ICON_LABELS)

    def test_basic_meal(self):
        meal = self.build(make_dish())
        self.assertEqual(meal["name"], "Testgericht")
        self.assertEqual(meal["diet"], "vegan")
        self.assertEqual(meal["choice_sides"], 2)
        self.assertEqual(meal["mealtime"], "lunch")
        self.assertEqual(meal["serving"]["location"], "EG Nord")
        self.assertFalse(meal["is_side"])
        self.assertEqual(meal["prices"]["student"], 3.3)
        self.assertEqual(meal["prices"]["employee"], 5.6)

    def test_junk_dish_discarded(self):
        meal = self.build(make_dish(name="Tagesrestproduktion", components=["Tagesrestproduktion"]))
        self.assertIsNone(meal)

    def test_side_dish_by_line(self):
        meal = self.build(make_dish(name="Reis", menu_type="xBeilagen", components=["Reis"], price=0.65))
        self.assertTrue(meal["is_side"])

    def test_side_dish_by_price_without_line(self):
        meal = self.build(make_dish(name="Salat", menu_type="", components=["Salat"], price=0.65))
        self.assertTrue(meal["is_side"])

    def test_expensive_lineless_dish_is_main(self):
        meal = self.build(make_dish(name="Tagesgericht", menu_type="", components=["Tagesgericht"], price=4.5))
        self.assertFalse(meal["is_side"])

    def test_bistro_name_with_trailing_codes(self):
        dish = make_dish(
            name_de="Bami Goreng 11, 13, 16, 21",
            components=[],
            menu_type="",
            allergens_numbers="",
            price=2.5,
        )
        meal = self.build(dish)
        self.assertEqual(meal["name"], "Bami Goreng")
        self.assertEqual([a["code"] for a in meal["allergens"]], ["11", "13", "16", "21"])

    def test_allergen_hints_in_parens_stripped(self):
        dish = make_dish(components=["Hähnchen-Crossies (11w,11)", "Sweet-Chili Dip"])
        meal = self.build(dish)
        self.assertEqual(meal["name"], "Hähnchen-Crossies")
        self.assertEqual([c["name"] for c in meal["components"]], ["Sweet-Chili Dip"])


class MergeDuplicateMealsTest(unittest.TestCase):
    def build(self, dish):
        fields = importer.custom_fields_to_dict(dish.get("custom_fields"))
        return importer.build_meal(dish, fields, {}, importer.DEFAULT_FOOD_ICON_LABELS)

    def test_two_time_different_location(self):
        lunch = self.build(make_dish(menu_type="QUERBEET ST", dish_info="EG Süd 11:30 - 14:30 Uhr"))
        dinner = self.build(make_dish(menu_type="QUERBEET ABENDESSEN", dish_info="MG Nord 14.30 - 18.15 Uhr"))
        merged = importer.merge_duplicate_meals([lunch, dinner])
        self.assertEqual(len(merged), 1)
        meal = merged[0]
        self.assertTrue(meal["throughout"])
        self.assertEqual(meal["mealtimes"], ["lunch", "dinner"])
        self.assertEqual(len(meal["servings"]), 2)
        self.assertEqual(meal["serving"]["location"], "EG Süd")  # earliest first
        self.assertEqual(meal["mealtime"], "lunch")

    def test_two_time_same_location(self):
        # served at the same counter at both times: must still report both
        # mealtimes so it can appear in both sections
        lunch = self.build(make_dish(menu_type="QUERBEET ST", dish_info="MG Nord 11.30 - 14.15 Uhr"))
        dinner = self.build(make_dish(menu_type="QUERBEET ABENDESSEN", dish_info="MG Nord 14.30 - 18.15 Uhr"))
        merged = importer.merge_duplicate_meals([lunch, dinner])
        self.assertEqual(len(merged), 1)
        meal = merged[0]
        self.assertTrue(meal["throughout"])
        self.assertEqual(meal["mealtimes"], ["lunch", "dinner"])
        self.assertEqual(len(meal["servings"]), 2)

    def test_dinner_only_instance_without_serving_info(self):
        lunch = self.build(make_dish(menu_type="HEIMSPIEL ST", dish_info=""))
        dinner = self.build(make_dish(menu_type="HEIMSPIEL ABENDESSEN", dish_info="MG Nord 14.30 - 18.15 Uhr"))
        merged = importer.merge_duplicate_meals([lunch, dinner])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["mealtimes"], ["lunch", "dinner"])
        self.assertTrue(merged[0]["throughout"])

    def test_different_prices_not_merged(self):
        cheap = self.build(make_dish(name="Pommes frites", components=["Pommes frites"], price=0.65, menu_type=""))
        full = self.build(make_dish(name="Pommes frites", components=["Pommes frites", "Veganes Gyros"], price=4.3))
        merged = importer.merge_duplicate_meals([cheap, full])
        self.assertEqual(len(merged), 2)

    def test_identical_duplicates_collapse(self):
        a = self.build(make_dish(menu_type="MEISTERWERK", dish_info="2"))
        b = self.build(make_dish(menu_type="MEISTERWERK AKTION", dish_info="2"))
        merged = importer.merge_duplicate_meals([a, b])
        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0]["throughout"])
        self.assertEqual(merged[0]["mealtimes"], ["lunch"])


class MatchCanteenTest(unittest.TestCase):
    canteens = {
        "unimensa": {"ort_id": "201", "screen_locations": ["Mensa Zülpicher Straße"]},
        "bistro": {"screen_locations": ["Bistro Lindenthal", "Bistro Lindenthal - Warmausgabe"]},
    }

    def test_ort_id_authoritative(self):
        dish = {"custom_fields": [{"field_id": "ort_id", "value": "201"}], "screens": []}
        fields = importer.custom_fields_to_dict(dish["custom_fields"])
        self.assertEqual(importer.match_canteen(dish, fields, self.canteens), "unimensa")

    def test_unknown_ort_id_unmatched_despite_screens(self):
        dish = {
            "custom_fields": [{"field_id": "ort_id", "value": "999"}],
            "screens": [{"location": "Bistro Lindenthal"}],
        }
        fields = importer.custom_fields_to_dict(dish["custom_fields"])
        self.assertIsNone(importer.match_canteen(dish, fields, self.canteens))

    def test_screen_fallback(self):
        dish = {"custom_fields": [], "screens": [{"location": "Bistro Lindenthal - Warmausgabe"}]}
        fields = importer.custom_fields_to_dict(dish["custom_fields"])
        self.assertEqual(importer.match_canteen(dish, fields, self.canteens), "bistro")


class NormalizeMenuTest(unittest.TestCase):
    def test_end_to_end(self):
        canteens = {"unimensa": {"ort_id": "201", "screen_locations": []}}
        menu_json = [
            {
                "date": "2026-06-15",
                "dishes": [
                    make_dish(menu_type="QUERBEET ST", dish_info="EG Süd 11:30 - 14:30 Uhr"),
                    make_dish(menu_type="QUERBEET ABENDESSEN", dish_info="MG Nord 14.30 - 18.15 Uhr"),
                    make_dish(name="Tagesrestproduktion", components=["Tagesrestproduktion"]),
                    make_dish(name="Reis", menu_type="xBeilagen", components=["Reis"], price=0.65),
                    make_dish(name="Anderswo", ort_id="999"),
                ],
            }
        ]
        result = importer.normalize_menu(menu_json, canteens, importer.DEFAULT_FOOD_ICON_LABELS)
        meals = result["menu"]["unimensa"]["2026-06-15"]
        names = [m["name"] for m in meals]
        self.assertIn("Testgericht", names)
        self.assertIn("Reis", names)
        self.assertNotIn("Tagesrestproduktion", names)
        self.assertNotIn("Anderswo", names)
        self.assertEqual(len([m for m in meals if m["name"] == "Testgericht"]), 1)
        test_meal = next(m for m in meals if m["name"] == "Testgericht")
        self.assertTrue(test_meal["throughout"])
        self.assertEqual(result["days"], ["2026-06-15"])
        # allergen legend gets merged from allergens_names fields (none here)
        self.assertEqual(result["allergen_legend"], {})


class AllergenLegendTest(unittest.TestCase):
    def test_parse(self):
        legend = {}
        importer.parse_allergen_legend(
            "11w=Enthält Weizen Gluten | wheat, 17=Enthält Milch | contains milk", legend
        )
        self.assertEqual(legend["11w"], {"de": "Enthält Weizen Gluten", "en": "wheat"})
        self.assertEqual(legend["17"], {"de": "Enthält Milch", "en": "contains milk"})


if __name__ == "__main__":
    unittest.main()
