# Spotter — Weather-Aware Truck Routing

A full-stack web application that helps truck drivers and fleet dispatchers evaluate and select the safest commercial driving routes based on real-time weather forecasts, truck load weight, and travel time.

Built with **Django REST Framework** (Backend) and **React + Vite + Leaflet** (Frontend).

---

## Key Features

1. **Intelligent Route Generation & Sampling**:
   - Generates up to 3 driving route alternatives using the OSRM Driving API.
   - Samples checkpoints every **10, 25, or 50 miles** along each route corridor.
   - Computes accurate truck Estimated Arrival Times (ETA) at each checkpoint based on driving duration and distance.
   - Implements **100% complete route-mile accounting**: every mile from origin to destination is represented in checkpoint segment metrics.

2. **Accurate Weather Risk & Load Weight Rules**:
   - Classifies risk at each checkpoint into **Low**, **Moderate**, **High**, **Severe**, or **No Travel** based on the assessment thresholds:
     - **Wind**: `<25 mph` Low, `25–34` Moderate, `35–44` High, `45–54` Severe, `≥55 mph` No Travel.
     - **Rain**: `<0.10 in/hr` Low, `0.10–0.25` Moderate, `0.25–0.50` High, `0.50–1.00` Severe, `>1.00 in/hr` No Travel.
     - **Snow**: `<0.5 in/hr` Low, `0.5–1.0` Moderate, `1.0–2.0` High, `2.0–3.0` Severe, `>3.0 in/hr` No Travel.
   - Enforces truck load weight safety overrides:
     - `≥55 mph wind` &rarr; **No Travel** for any load weight.
     - `45–54 mph wind + >30,000 lb` &rarr; **No Travel**.
     - `35–44 mph wind + >40,000 lb` &rarr; **Severe**.

3. **Exact 4-Tier Lexicographical Route Ranking**:
   Routes are ranked strictly according to the assessment preference criteria:
   1. **Fewest Severe miles**
   2. **Fewest High miles**
   3. **Lowest average risk**
   4. **Shortest travel time**
   - *No Travel Advisory*: Routes with any segment exceeding safe operating thresholds display a prominent `⛔ NO TRAVEL ADVISORY` banner without silently inserting unrequested ranking filters.

4. **Interactive Corridor Weather Heatmap & 0–48h Timeline**:
   - High-performance Canvas Heatmap overlay rendered directly along the active route corridor.
   - **0–48 Hour Forecast Slider**: dynamically steps through the 48-hour forecast horizon on the client at 60 FPS without server latency.
   - **Interactive Play/Pause Animation**: auto-advances through the 48-hour timeline to visualize weather system movements across the corridor.
   - **Multi-Metric Switcher**: toggle between **Overall Risk**, **Wind Speed (mph)**, **Rain Rate (in/hr)**, and **Snowfall Rate (in/hr)**.
   - Out-of-range or missing forecast data is clearly flagged; weather data is never fabricated.

5. **High-Performance Weather Batching**:
   - Batches Open-Meteo coordinate queries into chunked multi-location requests (`latitude=lat1,lat2&longitude=lon1,lon2`).
   - Reduces API calls from 150+ sequential requests down to 1–3 requests, slashing response latency from ~45 seconds to **under 2 seconds** and eliminating timeouts.
   - Fixes snowfall unit calculation (uses native inches; no division by 2.54) and aggregates convective showers with stratiform rain.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+ and npm

---

### Backend Setup

```powershell
cd backend

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Linux/macOS: source .venv/bin/activate

# Install dependencies
python -m pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Run development server
python manage.py runserver
```
Backend API will be live at `http://127.0.0.1:8000/`.

---

### Frontend Setup

```powershell
cd frontend

# Install dependencies
npm install

# Run Vite development server
npm run dev
```
Frontend will be live at `http://localhost:5173/`.

---

## Running Automated Tests

A comprehensive test suite with 15 test cases verifies risk thresholds, load overrides, ranking tie-breaks, mileage accounting, snowfall units, and API validation:

```powershell
cd backend
.\.venv\Scripts\python.exe manage.py test routing
```

To run the frontend production build test:
```powershell
cd frontend
npm run build
```

---

## API Specification

### `POST /api/analyze/`

#### Request Body
```json
{
  "origin": "Chicago, IL",
  "destination": "Denver, CO",
  "departure_time": "2026-10-17T12:00:00Z",
  "load_weight_lb": 35000,
  "checkpoint_interval_miles": 25
}
```

#### Response Structure
```json
{
  "routes": [
    {
      "id": "route-1",
      "name": "Route 1",
      "distance_miles": 1004.77,
      "duration_seconds": 64291.1,
      "rank": 1,
      "recommended": true,
      "severe_miles": 0.0,
      "high_miles": 0.0,
      "no_travel_miles": 0.0,
      "has_no_travel": false,
      "total_checkpoint_miles": 1004.77,
      "average_risk": 0.0,
      "geometry": [[-87.65, 41.85], ...],
      "checkpoints": [
        {
          "distance_miles": 25.0,
          "segment_miles": 25.0,
          "latitude": 41.72,
          "longitude": -88.11,
          "eta": "2026-10-17T12:26:39+00:00",
          "wind_mph": 12.4,
          "wind_gusts_mph": 22.1,
          "rain_in_hr": 0.0,
          "snow_in_hr": 0.0,
          "risk": "low",
          "risk_score": 0,
          "hourly_forecast": [
            { "hour": 0, "wind_mph": 12.4, "rain_in_hr": 0.0, "snow_in_hr": 0.0, "risk": "low", "risk_score": 0, "available": true },
            ...
          ]
        }
      ]
    }
  ],
  "recommendation": "route-1",
  "route_count": 2,
  "warning": "Routing provider returned 2 route option(s) for this corridor (up to 3 requested)."
}
```

---

## Production Deployment Readiness

The backend configuration in `backend/config/settings.py` supports standard environment variables:

| Environment Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Secret key for cryptographic signing | `dev-only-change-before-deploy` |
| `DJANGO_DEBUG` | Enable/disable debug mode (`True`/`False`) | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hostnames | `localhost,127.0.0.1` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed CORS origins | `http://localhost:5173,http://127.0.0.1:5173` |
| `CORS_ALLOW_ALL` | Allow all CORS origins (useful for initial cloud testing) | `False` |

In Linux production containers (Docker, Render, Railway, Fly.io), Gunicorn is included:
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

For the frontend, configure `VITE_API_URL` to point to the deployed backend URL during build time:
```bash
VITE_API_URL=https://your-api.onrender.com npm run build
```

---

## Provider Limits & Design Decisions

1. **OSRM Route Alternatives**: The public OSRM demonstration server enforces strict heuristics on route alternatives (path-sharing threshold and stretch limit). For corridors such as Chicago &rarr; Denver, only 2 viable highway alternatives are returned. In compliance with the assessment guidelines, the application presents all available alternatives and clearly displays an informational banner rather than fabricating synthetic routes.
2. **Open-Meteo Rate Limits & Timeouts**: Individual per-checkpoint queries frequently exceeded network timeouts and rate limits. The multi-location coordinate batching implemented in `services.py` combines checkpoint queries into chunks of 25–30 coordinates, executing route analyses in under 2 seconds.
3. **Forecast Horizon & Boundary Safety**: Open-Meteo 7-day hourly forecasts are utilized so that departures scheduled for future dates or multi-day journeys always have hourly forecast coverage. Any timestamp outside the available horizon is explicitly flagged as unavailable rather than estimating or fabricating weather data.
