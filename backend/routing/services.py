from datetime import datetime, timedelta, timezone
import math
import time
import requests
import logging
from .risk import classify_checkpoint

logger = logging.getLogger(__name__)

MILES_PER_METER = 1 / 1609.344


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

# In-memory caches to prevent redundant API queries
_GEOCODE_CACHE = {}
_WEATHER_CACHE = {}
_CACHE_TTL_SECONDS = 900  # 15 minutes


class ProviderError(RuntimeError):
    pass


CITY_ALIASES = {
    "dhn": "Dhanbad, India",
    "cnb": "Kanpur, India",
    "del": "Delhi, India",
    "ndls": "New Delhi, India",
    "bom": "Mumbai, India",
    "ccu": "Kolkata, India",
    "hwh": "Howrah, India",
    "pnbe": "Patna, India",
    "chi": "Chicago, IL",
    "den": "Denver, CO",
    "nyc": "New York, NY",
    "lax": "Los Angeles, CA",
    "sfo": "San Francisco, CA",
    "sea": "Seattle, WA",
    "bos": "Boston, MA",
    "mia": "Miami, FL",
}


def geocode(place):
    place_str = str(place).strip()
    parts = place_str.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lon, lat
        except ValueError:
            pass

    cache_key = place_str.lower()
    if cache_key in CITY_ALIASES:
        place_str = CITY_ALIASES[cache_key]

    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    try:
        r = requests.get(
            GEOCODE_URL,
            params={"name": place_str, "count": 1, "language": "en", "format": "json"},
            timeout=8,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
    except requests.RequestException as exc:
        logger.exception("Geocoding failed for '%s'", place)
        raise ProviderError("Geocoding service unavailable") from exc
    if not results:
        raise ProviderError(f"Location not found: {place}")
    coords = (float(results[0]["longitude"]), float(results[0]["latitude"]))
    _GEOCODE_CACHE[cache_key] = coords
    return coords


def get_routes(origin, destination):
    a, b = geocode(origin), geocode(destination)
    url = f"{OSRM_URL}/{a[0]},{a[1]};{b[0]},{b[1]}"
    try:
        r = requests.get(
            url,
            params={
                "alternatives": "3",
                "steps": "false",
                "overview": "full",
                "geometries": "geojson",
            },
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as exc:
        logger.exception("OSRM route request failed")
        raise ProviderError("Routing service unavailable") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ProviderError("No route found for these locations")

    return [
        {
            "id": f"route-{i+1}",
            "name": f"Route {i+1}",
            "distance_miles": round(item["distance"] * MILES_PER_METER, 2),
            "duration_seconds": round(item["duration"], 1),
            "geometry": item["geometry"]["coordinates"],
        }
        for i, item in enumerate(payload["routes"][:3])
    ]


def interpolate_checkpoints(route, interval_miles, departure):
    """
    Interpolates checkpoints along the route geometry every `interval_miles`,
    ensuring that the final checkpoint lands at the destination and the sum
    of all segment miles strictly equals the total route distance.
    """
    coords = route.get("geometry", [])
    if len(coords) < 2:
        return []

    cumulative = [0.0]
    for a, b in zip(coords, coords[1:]):
        lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        cumulative.append(cumulative[-1] + 3958.7613 * 2 * math.asin(min(1, math.sqrt(h))))

    geom_total = cumulative[-1]
    route_total = float(route.get("distance_miles", geom_total))
    if route_total <= 0:
        return []

    interval = float(interval_miles)
    targets = []
    curr = interval
    while curr < route_total - 0.5:
        targets.append(curr)
        curr += interval
    targets.append(route_total)

    out = []
    prev_d = 0.0
    for target in targets:
        seg_miles = round(target - prev_d, 2)
        prev_d = target

        if geom_total > 0:
            target_geom = (target / route_total) * geom_total
            i = next(
                (i for i in range(1, len(cumulative)) if cumulative[i] >= target_geom),
                len(coords) - 1,
            )
            seg_len = cumulative[i] - cumulative[i - 1]
            f = (target_geom - cumulative[i - 1]) / seg_len if seg_len > 0 else 0.0
            lon = coords[i - 1][0] + f * (coords[i][0] - coords[i - 1][0])
            lat = coords[i - 1][1] + f * (coords[i][1] - coords[i - 1][1])
        else:
            lon, lat = coords[-1][0], coords[-1][1]

        fraction = min(1.0, max(0.0, target / route_total))
        eta = departure + timedelta(seconds=route.get("duration_seconds", 0) * fraction)

        out.append({
            "distance_miles": round(target, 2),
            "segment_miles": seg_miles,
            "longitude": round(lon, 5),
            "latitude": round(lat, 5),
            "eta": eta.isoformat(),
        })

    return out


def _parse_hourly_entry(hourly, idx, times_len):
    """Safely extracts weather parameters at hourly index idx."""
    wind = float((hourly.get("wind_speed_10m") or [0] * times_len)[idx] or 0)
    rain = float((hourly.get("rain") or [0] * times_len)[idx] or 0)
    showers = float((hourly.get("showers") or [0] * times_len)[idx] or 0)
    precip = float((hourly.get("precipitation") or [0] * times_len)[idx] or 0)
    snow = float((hourly.get("snowfall") or [0] * times_len)[idx] or 0)

    total_rain = round(rain + showers, 3)
    if total_rain == 0 and precip > 0:
        total_rain = round(max(0.0, precip - snow), 3)

    return {
        "wind_mph": round(wind, 1),
        "rain_in_hr": round(total_rain, 3),
        "snow_in_hr": round(snow, 3),
    }


def _sample_corridor_stations(checkpoints, max_stations=5):
    """Picks up to max_stations evenly spaced checkpoints along the corridor."""
    if len(checkpoints) <= max_stations:
        return [(cp["latitude"], cp["longitude"]) for cp in checkpoints]
    step = (len(checkpoints) - 1) / (max_stations - 1)
    indices = [int(round(i * step)) for i in range(max_stations)]
    return [(checkpoints[idx]["latitude"], checkpoints[idx]["longitude"]) for idx in indices]


def batch_fetch_weather(checkpoints, departure, load_weight_lb=30000):
    """
    Fetches real weather for corridor stations using a fast, single batched query
    with in-memory caching. Handles provider timeouts and errors gracefully without
    ever crashing route evaluation or fabricating data.
    """
    if not checkpoints:
        return True

    now = time.time()
    departure_utc = departure.astimezone(timezone.utc) if departure.tzinfo else departure.replace(tzinfo=timezone.utc)
    dep_base_hour = departure_utc.replace(minute=0, second=0, microsecond=0)

    # Pick up to 5 representative stations along the trip corridor
    stations = _sample_corridor_stations(checkpoints, max_stations=5)

    # Check cache first
    cached_stations = {}
    missing_stations = []
    for s in stations:
        ck = (round(s[0], 1), round(s[1], 1))
        if ck in _WEATHER_CACHE and (now - _WEATHER_CACHE[ck][0]) < _CACHE_TTL_SECONDS:
            cached_stations[s] = _WEATHER_CACHE[ck][1]
        else:
            missing_stations.append(s)

    # If missing stations, fetch in one fast query with a 9-second timeout
    if missing_stations:
        lat_str = ",".join(f"{lat:.3f}" for lat, _ in missing_stations)
        lon_str = ",".join(f"{lon:.3f}" for _, lon in missing_stations)
        params = {
            "latitude": lat_str,
            "longitude": lon_str,
            "hourly": "wind_speed_10m,precipitation,rain,snowfall",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "forecast_days": 3,
        }

        try:
            r = requests.get(WEATHER_URL, params=params, timeout=9)
            if r.status_code == 200:
                payload = r.json()
                results = payload if isinstance(payload, list) else [payload]
                for s, item in zip(missing_stations, results):
                    hourly = item.get("hourly", {})
                    cached_stations[s] = hourly
                    ck = (round(s[0], 1), round(s[1], 1))
                    _WEATHER_CACHE[ck] = (now, hourly)
            else:
                logger.warning("Open-Meteo returned status %s: %s", r.status_code, r.text[:200])
        except Exception as exc:
            logger.warning("Open-Meteo query timed out or failed: %s", exc)

    # Determine if any weather data is available
    has_any_weather = bool(cached_stations)

    for cp in checkpoints:
        # Find nearest weather station
        hourly = None
        nearest_s = None
        if cached_stations:
            nearest_s = min(
                cached_stations.keys(),
                key=lambda s: (s[0] - cp["latitude"]) ** 2 + (s[1] - cp["longitude"]) ** 2,
            )
            hourly = cached_stations.get(nearest_s)

        times = hourly.get("time", []) if hourly else []
        times_len = len(times)

        # Precompute 0-48h forecast array
        hourly_forecast = []
        if times_len:
            first_dt = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc)
            last_dt = datetime.fromisoformat(times[-1]).replace(tzinfo=timezone.utc)

            for h in range(49):
                h_target = dep_base_hour + timedelta(hours=h)
                h_key = h_target.strftime("%Y-%m-%dT%H:00")
                if h_key in times:
                    h_idx = times.index(h_key)
                    w_entry = _parse_hourly_entry(hourly, h_idx, times_len)
                    r_entry = classify_checkpoint(w_entry, load_weight_lb)
                    hourly_forecast.append({
                        "hour": h,
                        "time": h_key,
                        "wind_mph": w_entry["wind_mph"],
                        "rain_in_hr": w_entry["rain_in_hr"],
                        "snow_in_hr": w_entry["snow_in_hr"],
                        "risk": r_entry["risk"],
                        "risk_score": r_entry["risk_score"],
                        "available": True,
                    })
                else:
                    hourly_forecast.append({
                        "hour": h,
                        "time": h_key,
                        "available": False,
                    })
        else:
            first_dt = last_dt = None

        cp["hourly_forecast"] = hourly_forecast

        eta_val = cp.get("eta")
        eta_dt = datetime.fromisoformat(eta_val) if isinstance(eta_val, str) else eta_val
        eta_utc = eta_dt.astimezone(timezone.utc) if eta_dt.tzinfo else eta_dt.replace(tzinfo=timezone.utc)

        dist_to_station = (
            haversine_miles(cp["latitude"], cp["longitude"], nearest_s[0], nearest_s[1])
            if nearest_s
            else None
        )
        cp["station_mapping"] = {
            "sampled_station_lat": round(nearest_s[0], 4) if nearest_s else None,
            "sampled_station_lon": round(nearest_s[1], 4) if nearest_s else None,
            "distance_to_station_miles": round(dist_to_station, 1) if dist_to_station is not None else None,
            "is_direct_observation": (dist_to_station is not None and dist_to_station < 2.0),
            "mapping_method": "corridor_nearest_station",
        }

        if first_dt and last_dt and (first_dt <= eta_utc <= last_dt):
            target_time = eta_utc + timedelta(minutes=30)
            key = target_time.strftime("%Y-%m-%dT%H:00")
            if key in times:
                idx = times.index(key)
            else:
                idx = min(
                    range(times_len),
                    key=lambda k: abs(
                        datetime.fromisoformat(times[k]).replace(tzinfo=timezone.utc) - eta_utc
                    ),
                )
            w = _parse_hourly_entry(hourly, idx, times_len)
            cp.update(w)
            cp.update(classify_checkpoint(w, load_weight_lb))
            cp["weather_available"] = True
        else:
            # Weather provider timed out, failed, or data out of range
            # Explicitly mark weather as unavailable (do NOT fabricate safe 0.0 values)
            cp.update({
                "wind_mph": None,
                "rain_in_hr": None,
                "snow_in_hr": None,
                "risk": "unavailable",
                "risk_score": None,
                "weather_available": False,
                "error": "Live weather data unavailable (provider timed out or out of range)",
            })

    return has_any_weather


def get_weather(lat, lon, eta, load_weight_lb=30000):
    dummy_cp = {"latitude": lat, "longitude": lon, "eta": eta.isoformat() if hasattr(eta, "isoformat") else str(eta)}
    batch_fetch_weather([dummy_cp], eta, load_weight_lb)
    return {
        "wind_mph": dummy_cp.get("wind_mph", 0.0),
        "rain_in_hr": dummy_cp.get("rain_in_hr", 0.0),
        "snow_in_hr": dummy_cp.get("snow_in_hr", 0.0),
    }
