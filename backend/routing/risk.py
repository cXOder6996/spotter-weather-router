RISK_SCORE = {"low": 0, "moderate": 1, "high": 2, "severe": 3, "no_travel": 4, "unavailable": -1}

def classify_wind(mph, load_weight_lb):
    if mph < 0:
        raise ValueError("Wind speed cannot be negative")
    if mph >= 55:
        return "no_travel"
    if mph >= 45:
        return "no_travel" if load_weight_lb > 30000 else "severe"
    if mph >= 35:
        return "severe" if load_weight_lb > 40000 else "high"
    if mph >= 25:
        return "moderate"
    return "low"

def classify_rate(value, kind):
    if value < 0:
        raise ValueError("Weather values cannot be negative")
    if kind == "rain":
        if value < .10: return "low"
        if value < .25: return "moderate"
        if value < .50: return "high"
        if value <= 1.00: return "severe"
        return "no_travel"
    if kind == "snow":
        if value < .5: return "low"
        if value < 1.0: return "moderate"
        if value < 2.0: return "high"
        if value <= 3.0: return "severe"
        return "no_travel"
    raise ValueError(f"Unknown condition: {kind}")

def classify_checkpoint(weather, load_weight_lb):
    wind = classify_wind(float(weather.get("wind_mph", 0)), load_weight_lb)
    rain = classify_rate(float(weather.get("rain_in_hr", 0)), "rain")
    snow = classify_rate(float(weather.get("snow_in_hr", 0)), "snow")
    overall = max((wind, rain, snow), key=lambda risk: RISK_SCORE[risk])
    return {
        "wind_risk": wind,
        "rain_risk": rain,
        "snow_risk": snow,
        "risk": overall,
        "risk_score": RISK_SCORE[overall],
    }

def summarize_route(route):
    severe = high = no_travel = total_miles = weighted = 0.0
    valid_miles = 0.0
    for cp in route.get("checkpoints", []):
        miles = max(0.0, float(cp.get("segment_miles", 0)))
        total_miles += miles
        risk = cp.get("risk")
        is_avail = cp.get("weather_available")
        if is_avail is None:
            is_avail = (risk is not None and risk != "unavailable")

        if is_avail and risk in RISK_SCORE and RISK_SCORE[risk] >= 0:
            valid_miles += miles
            weighted += RISK_SCORE[risk] * miles
            if risk == "severe":
                severe += miles
            elif risk == "high":
                high += miles
            elif risk == "no_travel":
                no_travel += miles

    # If weather was completely unavailable for this route, do NOT assign an artificially safe 0.0 score!
    if valid_miles == 0.0 and total_miles > 0:
        route.update(
            weather_available=False,
            severe_miles=None,
            high_miles=None,
            no_travel_miles=None,
            has_no_travel=False,
            total_checkpoint_miles=round(total_miles, 2),
            average_risk=None,
        )
    else:
        route.update(
            weather_available=True,
            severe_miles=round(severe, 2),
            high_miles=round(high, 2),
            no_travel_miles=round(no_travel, 2),
            has_no_travel=(no_travel > 0),
            total_checkpoint_miles=round(total_miles, 2),
            average_risk=round(weighted / valid_miles, 3) if valid_miles else 0.0,
        )
    return route

def rank_route(route):
    # Assessment exact 4-tier preference:
    # 1. Fewest Severe miles
    # 2. Fewest High miles
    # 3. Lowest average risk
    # 4. Shortest travel time
    #
    # Safety invariant: Routes with unavailable weather have None risk metrics.
    # We substitute float("inf") so unverified routes are never falsely prioritized over verified routes.
    severe = route.get("severe_miles")
    high = route.get("high_miles")
    avg = route.get("average_risk")
    duration = route.get("duration_seconds", float("inf"))

    return (
        float("inf") if severe is None else round(float(severe), 4),
        float("inf") if high is None else round(float(high), 4),
        float("inf") if avg is None else round(float(avg), 4),
        round(float(duration), 2),
    )

