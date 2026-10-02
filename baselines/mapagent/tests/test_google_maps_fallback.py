import unittest
from unittest.mock import patch

import googlemaps

import parallel_function_implementation as maps_tools


class GoogleMapsFallbackTest(unittest.TestCase):
    def test_nearby_places_filters_results_to_visual_radius(self):
        results = {
            "results": [
                {"name": "Inside", "geometry": {"location": {"lat": 0, "lng": 0.005}}},
                {"name": "Outside", "geometry": {"location": {"lat": 0, "lng": 0.02}}},
            ]
        }
        with (
            patch.object(maps_tools, "geocode", return_value={"lat": 0, "lng": 0}),
            patch.object(maps_tools.gmaps, "places", return_value=results) as search,
        ):
            output = maps_tools.nearby_places("park", "Center", "park", radius_km=1)

        self.assertIn("Inside", output)
        self.assertNotIn("Outside", output)
        self.assertEqual(search.call_args.kwargs["radius"], 1000)

    def test_missing_route_does_not_crash_the_batch(self):
        with patch.object(maps_tools.gmaps, "directions", return_value=[]):
            result = maps_tools.get_travel_info(
                "Hotel Saltel, California",
                "Science City - Kolkata",
                "driving",
            )

        self.assertEqual(result, ("Unavailable", "Unavailable"))

    def test_unresolvable_directions_are_unavailable(self):
        with patch.object(
            maps_tools.gmaps,
            "directions",
            side_effect=googlemaps.exceptions.ApiError("NOT_FOUND"),
        ):
            result = maps_tools.get_travel_info("Starbucks", "Central Park", "walking")

        self.assertEqual(result, ("Unavailable", "Unavailable"))

    def test_other_directions_api_errors_are_not_hidden(self):
        with patch.object(
            maps_tools.gmaps,
            "directions",
            side_effect=googlemaps.exceptions.ApiError("REQUEST_DENIED"),
        ):
            with self.assertRaises(googlemaps.exceptions.ApiError):
                maps_tools.get_travel_info("Starbucks", "Central Park", "walking")

    def test_place_without_weekday_hours_uses_na(self):
        with (
            patch.object(
                maps_tools.gmaps,
                "places",
                return_value={"results": [{"place_id": "place-1"}]},
            ),
            patch.object(
                maps_tools.gmaps,
                "place",
                return_value={
                    "result": {
                        "name": "Example",
                        "formatted_address": "Example address",
                        "types": ["point_of_interest"],
                        "opening_hours": {"open_now": True},
                    }
                },
            ),
        ):
            result = maps_tools.get_place_info("Example")

        self.assertEqual(result["weekdays_opening_hours"], "N/A")

    def test_no_place_search_result_does_not_use_missing_place_id(self):
        with (
            patch.object(maps_tools.gmaps, "places", return_value={"results": []}),
            patch.object(maps_tools.gmaps, "place") as detail_request,
        ):
            result = maps_tools.get_place_info("Ipanema Beach")

        detail_request.assert_not_called()
        self.assertEqual(result["name"], "Ipanema Beach")
        self.assertEqual(result["address"], "N/A")

    def test_trip_rechecks_outlier_near_other_places_and_routes_by_place_id(self):
        def info(name, place_id, lat, lng):
            return {
                "name": name, "address": name, "rating": "N/A", "types": [],
                "is_open_now": "N/A", "weekdays_opening_hours": "N/A",
                "_place_id": place_id, "_location": {"lat": lat, "lng": lng},
            }

        places = {
            ("Museum", None): info("Museum", "museum", 40.78, -73.96),
            ("Starbucks", None): info("Starbucks", "wrong", 38.58, -121.49),
            ("Park", None): info("Park", "park", 40.79, -73.97),
            ("Starbucks", (40.78, -73.96)): info("Starbucks", "nearby", 40.78, -73.97),
        }

        def get_info(name, location_bias=None):
            return places[(name, location_bias)]

        with (
            patch.object(maps_tools, "get_place_info", side_effect=get_info) as lookup,
            patch.object(maps_tools, "get_travel_info", return_value=("4 mins", "0.2 mi")) as route,
        ):
            result = maps_tools.trip("Museum", ["Starbucks", "Park"], "walking")

        lookup.assert_any_call("Starbucks", location_bias=(40.78, -73.96))
        route.assert_any_call("place_id:nearby", "place_id:park", "walking")
        self.assertIn("from Starbucks to Park is 4 mins", result)


if __name__ == "__main__":
    unittest.main()
