import json
from datetime import datetime, timezone, timedelta
from django.test import SimpleTestCase, RequestFactory
from .risk import (
    classify_wind,
    classify_rate,
    classify_checkpoint,
    summarize_route,
    rank_route,
)
from .services import (
    interpolate_checkpoints,
    _parse_hourly_entry,
    batch_fetch_weather,
    ProviderError,
)
from .views import analyze_route


class RiskTests(SimpleTestCase):
    def test_wind_and_load_rules(self):
        # Base wind brackets (< 30k lb load)
        self.assertEqual(classify_wind(0, 10000), "low")
        self.assertEqual(classify_wind(24.9, 10000), "low")
        self.assertEqual(classify_wind(25.0, 10000), "moderate")
        self.assertEqual(classify_wind(34.9, 10000), "moderate")
        self.assertEqual(classify_wind(35.0, 10000), "high")
        self.assertEqual(classify_wind(44.9, 10000), "high")
        self.assertEqual(classify_wind(45.0, 10000), "severe")
        self.assertEqual(classify_wind(54.9, 10000), "severe")
        self.assertEqual(classify_wind(55.0, 10000), "no_travel")
        self.assertEqual(classify_wind(75.0, 10000), "no_travel")

        # Load weight overrides:
        # 1. 35-44 mph + >40,000 lb -> Severe
        self.assertEqual(classify_wind(35.0, 40000), "high")
        self.assertEqual(classify_wind(35.0, 40001), "severe")
        self.assertEqual(classify_wind(40.0, 45000), "severe")

        # 2. 45-54 mph + >30,000 lb -> No Travel
        self.assertEqual(classify_wind(45.0, 30000), "severe")
        self.assertEqual(classify_wind(45.0, 30001), "no_travel")
        self.assertEqual(classify_wind(50.0, 35000), "no_travel")

        # 3. >=55 mph -> No Travel for any load
        self.assertEqual(classify_wind(55.0, 5000), "no_travel")
        self.assertEqual(classify_wind(55.0, 60000), "no_travel")

        with self.assertRaises(ValueError):
            classify_wind(-5, 10000)

    def test_rain_boundaries(self):
        self.assertEqual(classify_rate(0.0, "rain"), "low")
        self.assertEqual(classify_rate(0.099, "rain"), "low")
        self.assertEqual(classify_rate(0.10, "rain"), "moderate")
        self.assertEqual(classify_rate(0.249, "rain"), "moderate")
        self.assertEqual(classify_rate(0.25, "rain"), "high")
        self.assertEqual(classify_rate(0.499, "rain"), "high")
        self.assertEqual(classify_rate(0.50, "rain"), "severe")
        self.assertEqual(classify_rate(1.00, "rain"), "severe")
        self.assertEqual(classify_rate(1.001, "rain"), "no_travel")
        self.assertEqual(classify_rate(2.5, "rain"), "no_travel")

        with self.assertRaises(ValueError):
            classify_rate(-0.1, "rain")

    def test_snow_boundaries(self):
        self.assertEqual(classify_rate(0.0, "snow"), "low")
        self.assertEqual(classify_rate(0.49, "snow"), "low")
        self.assertEqual(classify_rate(0.5, "snow"), "moderate")
        self.assertEqual(classify_rate(0.99, "snow"), "moderate")
        self.assertEqual(classify_rate(1.0, "snow"), "high")
        self.assertEqual(classify_rate(1.99, "snow"), "high")
        self.assertEqual(classify_rate(2.0, "snow"), "severe")
        self.assertEqual(classify_rate(3.0, "snow"), "severe")
        self.assertEqual(classify_rate(3.01, "snow"), "no_travel")

        with self.assertRaises(ValueError):
            classify_rate(-0.5, "snow")

    def test_classify_checkpoint_overall_maximum(self):
        weather = {"wind_mph": 20, "rain_in_hr": 0.30, "snow_in_hr": 0.0}
        # wind: low, rain: high, snow: low -> overall: high
        res = classify_checkpoint(weather, 25000)
        self.assertEqual(res["risk"], "high")
        self.assertEqual(res["wind_risk"], "low")
        self.assertEqual(res["rain_risk"], "high")
        self.assertEqual(res["snow_risk"], "low")
        self.assertEqual(res["risk_score"], 2)

    def test_exact_4_tier_route_ranking(self):
        # Preference Order:
        # 1. Fewest Severe miles
        # 2. Fewest High miles
        # 3. Lowest average risk
        # 4. Shortest travel time

        # Test Priority 1: Fewest Severe miles wins regardless of other metrics
        r1 = {"severe_miles": 0.0, "high_miles": 50.0, "average_risk": 2.5, "duration_seconds": 9000}
        r2 = {"severe_miles": 5.0, "high_miles": 0.0, "average_risk": 0.5, "duration_seconds": 1000}
        self.assertLess(rank_route(r1), rank_route(r2))

        # Test Priority 2: Severe miles tie -> Fewest High miles wins
        r3 = {"severe_miles": 2.0, "high_miles": 10.0, "average_risk": 2.8, "duration_seconds": 8000}
        r4 = {"severe_miles": 2.0, "high_miles": 20.0, "average_risk": 1.5, "duration_seconds": 2000}
        self.assertLess(rank_route(r3), rank_route(r4))

        # Test Priority 3: Severe and High tie -> Lowest average risk wins
        r5 = {"severe_miles": 2.0, "high_miles": 10.0, "average_risk": 1.2, "duration_seconds": 8000}
        r6 = {"severe_miles": 2.0, "high_miles": 10.0, "average_risk": 1.8, "duration_seconds": 3000}
        self.assertLess(rank_route(r5), rank_route(r6))

        # Test Priority 4: All risks tie -> Shortest travel time wins
        r7 = {"severe_miles": 2.0, "high_miles": 10.0, "average_risk": 1.5, "duration_seconds": 3600}
        r8 = {"severe_miles": 2.0, "high_miles": 10.0, "average_risk": 1.5, "duration_seconds": 7200}
        self.assertLess(rank_route(r7), rank_route(r8))

    def test_summarize_route_and_no_travel_flag(self):
        route = {
            "checkpoints": [
                {"segment_miles": 25.0, "risk": "low"},
                {"segment_miles": 25.0, "risk": "moderate"},
                {"segment_miles": 25.0, "risk": "severe"},
                {"segment_miles": 10.0, "risk": "no_travel"},
            ]
        }
        summarize_route(route)
        self.assertEqual(route["total_checkpoint_miles"], 85.0)
        self.assertEqual(route["severe_miles"], 25.0)
        self.assertEqual(route["no_travel_miles"], 10.0)
        self.assertTrue(route["has_no_travel"])
        # Expected weighted: 25*0 + 25*1 + 25*3 + 10*4 = 0 + 25 + 75 + 40 = 140
        # Avg risk: 140 / 85 = 1.647
        self.assertAlmostEqual(route["average_risk"], 1.647, places=2)

    def test_unavailable_weather_not_artificially_safe(self):
        # When weather provider fails or data is unavailable,
        # route must produce an explicit unavailable state, NOT artificially safe 0.0 scores
        route = {
            "checkpoints": [
                {"segment_miles": 25.0, "risk": "unavailable", "weather_available": False},
                {"segment_miles": 25.0, "risk": "unavailable", "weather_available": False},
            ],
            "duration_seconds": 3600,
        }
        summarize_route(route)
        self.assertFalse(route["weather_available"])
        self.assertIsNone(route["severe_miles"])
        self.assertIsNone(route["high_miles"])
        self.assertIsNone(route["average_risk"])

        # In ranking, unverified risk evaluates as float('inf'), never 0
        score = rank_route(route)
        self.assertEqual(score[0], float("inf"))
        self.assertEqual(score[1], float("inf"))
        self.assertEqual(score[2], float("inf"))
        self.assertEqual(score[3], 3600.0)


class ServiceAndInterpolationTests(SimpleTestCase):
    def test_checkpoint_interpolation_complete_mileage(self):
        departure = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        # Create a sample geometry line of ~1000 miles
        coords = [[-87.65 + i * 0.17, 41.85 - i * 0.02] for i in range(100)]
        route = {
            "geometry": coords,
            "distance_miles": 1004.77,
            "duration_seconds": 64000.0,
        }

        checkpoints = interpolate_checkpoints(route, 25, departure)
        self.assertGreater(len(checkpoints), 0)

        # Check total mile coverage
        sum_segments = sum(cp["segment_miles"] for cp in checkpoints)
        self.assertAlmostEqual(sum_segments, route["distance_miles"], places=1)
        self.assertEqual(checkpoints[-1]["distance_miles"], route["distance_miles"])

        # Check ETA progression
        etas = [datetime.fromisoformat(cp["eta"]) for cp in checkpoints]
        for e1, e2 in zip(etas, etas[1:]):
            self.assertLessEqual(e1, e2)

        # Final ETA matches route duration
        expected_final_eta = departure + timedelta(seconds=route["duration_seconds"])
        self.assertEqual(etas[-1], expected_final_eta)

    def test_short_route_interpolation(self):
        departure = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        route = {
            "geometry": [[-87.65, 41.85], [-87.60, 41.80]],
            "distance_miles": 8.5,
            "duration_seconds": 600.0,
        }
        # Interval is 25 miles, longer than the 8.5 mile route
        checkpoints = interpolate_checkpoints(route, 25, departure)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["distance_miles"], 8.5)
        self.assertEqual(checkpoints[0]["segment_miles"], 8.5)

    def test_snow_unit_not_divided_by_2_54(self):
        hourly = {
            "time": ["2026-10-17T12:00"],
            "wind_speed_10m": [15.0],
            "wind_gusts_10m": [22.0],
            "rain": [0.0],
            "showers": [0.0],
            "precipitation": [2.5],
            "snowfall": [2.5],  # 2.5 inches of snow
            "weather_code": [71],
        }
        parsed = _parse_hourly_entry(hourly, 0, 1)
        # Snowfall MUST be 2.5 in/hr, NOT 2.5 / 2.54 (~0.98 in/hr)
        self.assertEqual(parsed["snow_in_hr"], 2.5)

    def test_rain_includes_convective_showers(self):
        hourly = {
            "time": ["2026-10-17T12:00"],
            "wind_speed_10m": [10.0],
            "rain": [0.15],
            "showers": [0.20],
            "precipitation": [0.35],
            "snowfall": [0.0],
            "weather_code": [80],
        }
        parsed = _parse_hourly_entry(hourly, 0, 1)
        # Total rain rate combines rain (0.15) + showers (0.20) = 0.35 in/hr
        self.assertEqual(parsed["rain_in_hr"], 0.35)

    def test_checkpoint_explicit_station_mapping(self):
        departure = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        checkpoints = [
            {"latitude": 40.8, "longitude": -96.7, "eta": departure.isoformat(), "segment_miles": 25.0},
            {"latitude": 40.5, "longitude": -98.5, "eta": (departure + timedelta(hours=1)).isoformat(), "segment_miles": 25.0},
        ]
        # Run batch_fetch_weather
        batch_fetch_weather(checkpoints, departure, 30000)
        for cp in checkpoints:
            self.assertIn("station_mapping", cp)
            mapping = cp["station_mapping"]
            self.assertIn("sampled_station_lat", mapping)
            self.assertIn("sampled_station_lon", mapping)
            self.assertIn("distance_to_station_miles", mapping)
            self.assertIn("is_direct_observation", mapping)
            self.assertEqual(mapping["mapping_method"], "corridor_nearest_station")


class ApiEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_post_required(self):
        req = self.factory.get("/api/analyze/")
        res = analyze_route(req)
        self.assertEqual(res.status_code, 405)

    def test_missing_origin_or_destination(self):
        req = self.factory.post(
            "/api/analyze/",
            data=json.dumps({"origin": "", "destination": "Denver, CO", "load_weight_lb": 25000, "departure_time": "2026-10-17T12:00:00Z"}),
            content_type="application/json",
        )
        res = analyze_route(req)
        self.assertEqual(res.status_code, 400)
        self.assertIn("required", json.loads(res.content)["error"])

    def test_negative_load_weight_rejected(self):
        req = self.factory.post(
            "/api/analyze/",
            data=json.dumps({"origin": "Chicago, IL", "destination": "Denver, CO", "load_weight_lb": -500, "departure_time": "2026-10-17T12:00:00Z"}),
            content_type="application/json",
        )
        res = analyze_route(req)
        self.assertEqual(res.status_code, 400)
        self.assertIn("negative", json.loads(res.content)["error"])

    def test_invalid_interval_rejected(self):
        req = self.factory.post(
            "/api/analyze/",
            data=json.dumps({"origin": "Chicago, IL", "destination": "Denver, CO", "load_weight_lb": 25000, "checkpoint_interval_miles": 30, "departure_time": "2026-10-17T12:00:00Z"}),
            content_type="application/json",
        )
        res = analyze_route(req)
        self.assertEqual(res.status_code, 400)
        self.assertIn("10, 25, or 50", json.loads(res.content)["error"])

    def test_missing_timezone_rejected(self):
        req = self.factory.post(
            "/api/analyze/",
            data=json.dumps({"origin": "Chicago, IL", "destination": "Denver, CO", "load_weight_lb": 25000, "departure_time": "2026-10-17T12:00:00"}),
            content_type="application/json",
        )
        res = analyze_route(req)
        self.assertEqual(res.status_code, 400)
        self.assertIn("timezone", json.loads(res.content)["error"])
