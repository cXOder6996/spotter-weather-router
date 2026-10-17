import json
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .services import (
    get_routes,
    interpolate_checkpoints,
    batch_fetch_weather,
    ProviderError,
)
from .risk import summarize_route, rank_route


def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "service": "spotter-weather-router",
        "version": "2.0.0",
    })


@csrf_exempt
def analyze_route(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body or "{}")
        origin = str(body.get("origin", "")).strip()
        destination = str(body.get("destination", "")).strip()
        load_val = body.get("load_weight_lb")
        if load_val is None:
            raise ValueError("Load weight is required")
        load = float(load_val)
        interval = int(body.get("checkpoint_interval_miles", 25))
        dep_str = str(body.get("departure_time", "")).strip()

        if not origin or not destination:
            raise ValueError("Origin and destination are required")
        if load < 0:
            raise ValueError("Load weight cannot be negative")
        if interval not in (10, 25, 50):
            raise ValueError("Checkpoint interval must be 10, 25, or 50 miles")
        if not dep_str:
            raise ValueError("Departure time is required")

        departure = datetime.fromisoformat(dep_str.replace("Z", "+00:00"))
        if departure.tzinfo is None:
            raise ValueError("Departure time must include timezone")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": f"Invalid request: {exc}"}, status=400)

    try:
        routes = get_routes(origin, destination)
        all_checkpoints = []
        for route in routes:
            checkpoints = interpolate_checkpoints(route, interval, departure)
            route["checkpoints"] = checkpoints
            all_checkpoints.extend(checkpoints)

        # Batch weather fetching across all checkpoints (resilient with 5s timeout)
        weather_ok = batch_fetch_weather(all_checkpoints, departure, load)

        # Summarize risks and aggregate miles for each route
        for route in routes:
            summarize_route(route)
            if not route.get("weather_available", True):
                route["advisory"] = "WEATHER SERVICE UNAVAILABLE: Live weather data could not be retrieved from provider. Safety metrics cannot be determined."
            elif route.get("has_no_travel"):
                route["advisory"] = "NO TRAVEL ADVISORY: Extreme weather conditions exceed safe commercial travel thresholds."
            else:
                route["advisory"] = None

        # Sort using the assessment's exact 4-tier lexicographical ranking tuple:
        # (severe_miles, high_miles, average_risk, duration_seconds)
        routes.sort(key=rank_route)

        for i, route in enumerate(routes):
            route["rank"] = i + 1
            route["recommended"] = (i == 0)

        warnings = []
        if len(routes) < 3:
            warnings.append(f"Routing provider returned {len(routes)} route option(s) for this corridor (up to 3 requested).")
        if not weather_ok:
            warnings.append("Live weather service timed out. Route options and ETAs are displayed. Click 'Analyze Routes' to retry weather.")

        return JsonResponse({
            "routes": routes,
            "recommendation": routes[0]["id"] if routes else None,
            "route_count": len(routes),
            "weather_available": weather_ok,
            "warning": " ".join(warnings) if warnings else None,
            "departure_time": departure.isoformat(),
            "load_weight_lb": load,
            "units": {
                "distance": "miles",
                "wind": "mph",
                "rain": "in/hr",
                "snow": "in/hr",
                "time": "ISO 8601 UTC",
            },
        })
    except ProviderError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    except Exception as exc:
        return JsonResponse({"error": f"Unexpected server error: {exc}"}, status=500)
