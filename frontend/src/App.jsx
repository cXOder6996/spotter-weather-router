import { useState, useEffect, useRef } from 'react'
import { MapContainer, TileLayer, Polyline, CircleMarker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import HeatmapLayer from './HeatmapLayer'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

const RISK_COLORS = {
  low: '#10b981',
  moderate: '#f59e0b',
  high: '#f97316',
  severe: '#ef4444',
  no_travel: '#991b1b',
  unavailable: '#64748b',
}

const HEAT_GRADIENTS = {
  risk: { 0.15: '#10b981', 0.4: '#f59e0b', 0.65: '#f97316', 0.85: '#ef4444', 1.0: '#991b1b' },
  wind: { 0.2: '#38bdf8', 0.4: '#3b82f6', 0.65: '#eab308', 0.85: '#ef4444', 1.0: '#7f1d1d' },
  rain: { 0.15: '#60a5fa', 0.35: '#2563eb', 0.6: '#1d4ed8', 0.85: '#7c3aed', 1.0: '#991b1b' },
  snow: { 0.2: '#a5f3fc', 0.45: '#38bdf8', 0.7: '#818cf8', 0.9: '#c084fc', 1.0: '#f43f5e' },
}

function AutoFitBounds({ activeRoute, routes }) {
  const map = useMap()
  useEffect(() => {
    const targetRoute = activeRoute || routes?.[0]
    if (!targetRoute?.geometry?.length) return
    const latLngs = targetRoute.geometry.map(([lon, lat]) => [lat, lon])
    if (latLngs.length > 1) {
      map.fitBounds(L.latLngBounds(latLngs), { padding: [35, 35] })
    }
  }, [activeRoute?.id, routes?.length, map])
  return null
}

export default function App() {
  const [form, setForm] = useState({
    origin: 'Chicago, IL',
    destination: 'Denver, CO',
    departure_time: new Date(Date.now() + 3600000).toISOString().slice(0, 16),
    load_weight_lb: '35000',
    checkpoint_interval_miles: '25',
  })
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)

  // 0-48h Heatmap Controls
  const [sliderHour, setSliderHour] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [heatMetric, setHeatMetric] = useState('risk')
  const [showHeatmap, setShowHeatmap] = useState(true)
  const [showCheckpoints, setShowCheckpoints] = useState(true)

  const playTimerRef = useRef(null)

  const update = (e) => setForm({ ...form, [e.target.name]: e.target.value })

  async function submit(e) {
    if (e) e.preventDefault()
    setBusy(true)
    setError('')
    setIsPlaying(false)
    try {
      const depDate = new Date(form.departure_time)
      const res = await fetch(`${API}/api/analyze/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin: form.origin.trim(),
          destination: form.destination.trim(),
          departure_time: depDate.toISOString(),
          load_weight_lb: Number(form.load_weight_lb),
          checkpoint_interval_miles: Number(form.checkpoint_interval_miles),
        }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.error || 'Route analysis failed')
      setData(body)
      setSelected(body.recommendation || body.routes?.[0]?.id || null)
      setSliderHour(0)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  // Auto-play forecast slider animation
  useEffect(() => {
    if (isPlaying) {
      playTimerRef.current = setInterval(() => {
        setSliderHour((prev) => (prev >= 48 ? 0 : prev + 1))
      }, 300)
    } else if (playTimerRef.current) {
      clearInterval(playTimerRef.current)
    }
    return () => {
      if (playTimerRef.current) clearInterval(playTimerRef.current)
    }
  }, [isPlaying])

  const routes = data?.routes || []
  const active = routes.find((r) => r.id === selected) || routes[0]

  // Calculate current timestamp for the 0-48h forecast slider
  const depBaseTime = form.departure_time ? new Date(form.departure_time) : new Date()
  const sliderCurrentTime = new Date(depBaseTime.getTime() + sliderHour * 3600000)

  // Compute heatmap points and corridor statistics from real forecast data
  const heatmapPoints = []
  let maxCorridorWind = 0
  let maxCorridorRain = 0
  let maxCorridorSnow = 0
  let worstCorridorRisk = 'low'
  const riskScores = { low: 0, moderate: 1, high: 2, severe: 3, no_travel: 4 }

  if (active?.checkpoints) {
    for (const cp of active.checkpoints) {
      const entry = cp.hourly_forecast?.[sliderHour]
      if (entry && entry.available) {
        if (entry.wind_mph > maxCorridorWind) maxCorridorWind = entry.wind_mph
        if (entry.rain_in_hr > maxCorridorRain) maxCorridorRain = entry.rain_in_hr
        if (entry.snow_in_hr > maxCorridorSnow) maxCorridorSnow = entry.snow_in_hr
        if ((riskScores[entry.risk] || 0) > (riskScores[worstCorridorRisk] || 0)) {
          worstCorridorRisk = entry.risk
        }

        let color = '#10b981'
        let opacity = 0.65

        if (heatMetric === 'risk') {
          color = RISK_COLORS[entry.risk] || '#10b981'
          opacity = entry.risk === 'low' ? 0.55 : 0.85
        } else if (heatMetric === 'wind') {
          const w = entry.wind_mph || 0
          if (w < 15) {
            color = '#0d9488'
            opacity = 0.35
          } else if (w < 25) {
            color = '#06b6d4'
            opacity = 0.60
          } else if (w < 35) {
            color = '#3b82f6'
            opacity = 0.75
          } else if (w < 45) {
            color = '#f59e0b'
            opacity = 0.85
          } else if (w < 55) {
            color = '#f97316'
            opacity = 0.90
          } else {
            color = '#dc2626'
            opacity = 0.95
          }
        } else if (heatMetric === 'rain') {
          const r = entry.rain_in_hr || 0
          if (r <= 0.001) {
            // Dry road - faint neutral slate teal
            color = '#1e3a45'
            opacity = 0.18
          } else if (r < 0.10) {
            // Light Rain (Sky Blue)
            color = '#38bdf8'
            opacity = 0.65
          } else if (r < 0.25) {
            // Moderate Rain (Royal Blue)
            color = '#2563eb'
            opacity = 0.78
          } else if (r < 0.50) {
            // Heavy Rain (Amber/Orange)
            color = '#f59e0b'
            opacity = 0.88
          } else if (r <= 1.00) {
            // Severe Rain (Crimson)
            color = '#ef4444'
            opacity = 0.92
          } else {
            // Extreme / No Travel Rain (Deep Burgundy)
            color = '#7f1d1d'
            opacity = 0.98
          }
        } else if (heatMetric === 'snow') {
          const s = entry.snow_in_hr || 0
          if (s <= 0.001) {
            // Clear road - faint neutral violet slate
            color = '#2e2744'
            opacity = 0.18
          } else if (s < 0.5) {
            // Light Snow (Ice White / Powder Lavender)
            color = '#e0e7ff'
            opacity = 0.72
          } else if (s < 1.0) {
            // Moderate Snow (Bright Violet)
            color = '#c084fc'
            opacity = 0.82
          } else if (s < 2.0) {
            // Heavy Snow (Deep Purple)
            color = '#9333ea'
            opacity = 0.88
          } else if (s <= 3.0) {
            // Severe Snow (Vibrant Magenta / Hot Pink)
            color = '#ec4899'
            opacity = 0.92
          } else {
            // Extreme / No Travel Snow (Dark Velvet Plum)
            color = '#831843'
            opacity = 0.98
          }
        }

        heatmapPoints.push([cp.latitude, cp.longitude, color, opacity])
      }
    }
  }

  const hasWeather = data?.weather_available !== false && Boolean(active?.checkpoints?.[0]?.hourly_forecast?.length)

  return (
    <main className="shell">
      <header>
        <div className="mark">S</div>
        <div>
          <b>
            SPOTTER <span>ROUTE INTELLIGENCE</span>
          </b>
          <small>Weather-aware commercial freight routing</small>
        </div>
        <aside>● WEATHER RISK ENGINE v2.0</aside>
      </header>

      <section className="hero">
        <div>
          <label>SAFER DECISIONS. SMARTER MILES.</label>
          <h1>
            Every mile, <em>forecast.</em>
          </h1>
          <p>Evaluate route safety against weather sampled at truck estimated arrival time.</p>
        </div>
        <div className="hero-stat">
          <strong>
            00—48 <small>HOUR CORRIDOR FORECAST</small>
          </strong>
        </div>
      </section>

      <section className="layout">
        {/* Left Panel: Configuration */}
        <aside className="panel">
          <label>01 / TRIP CONFIGURATION</label>
          <h2>Plan your route</h2>
          <form onSubmit={submit}>
            <label>
              ORIGIN
              <input name="origin" value={form.origin} onChange={update} placeholder="City, State" required />
            </label>
            <label>
              DESTINATION
              <input name="destination" value={form.destination} onChange={update} placeholder="City, State" required />
            </label>
            <label>
              DEPARTURE DATE & TIME
              <input type="datetime-local" name="departure_time" value={form.departure_time} onChange={update} required />
            </label>
            <div className="two">
              <label>
                LOAD WEIGHT (LB)
                <input
                  type="number"
                  min="0"
                  max="120000"
                  step="500"
                  name="load_weight_lb"
                  value={form.load_weight_lb}
                  onChange={update}
                  required
                />
              </label>
              <label>
                SAMPLING
                <select name="checkpoint_interval_miles" value={form.checkpoint_interval_miles} onChange={update}>
                  <option value="10">10 miles</option>
                  <option value="25">25 miles</option>
                  <option value="50">50 miles</option>
                </select>
              </label>
            </div>
            <button type="submit" disabled={busy}>
              {busy ? 'ANALYZING ROUTE WEATHER…' : 'ANALYZE ROUTES ↗'}
            </button>
          </form>

          {error && <div className="error-box">⚠️ {error}</div>}

          <div className="note">
            <b>Exact Ranking Priorities</b>
            <ol>
              <li>Fewest Severe miles</li>
              <li>Fewest High miles</li>
              <li>Lowest average risk</li>
              <li>Shortest travel time</li>
            </ol>
            <small>Load rules enforce Severe at &gt;40k lb wind and No Travel at &gt;30k lb or &ge;55 mph.</small>
          </div>

          <div className="legend-section">
            <span className="legend-title">RISK LEVELS</span>
            <div className="legend">
              {Object.entries(RISK_COLORS).map(([k, v]) => (
                <span key={k}>
                  <i style={{ background: v }} />
                  {k.replace('_', ' ').toUpperCase()}
                </span>
              ))}
            </div>
          </div>
        </aside>

        {/* Right Panel: Interactive Map & 0-48h Heatmap */}
        <section className="panel map-panel">
          <div className="map-header">
            <div>
              <label>02 / CORRIDOR FORECAST MAP</label>
              <h2>Interactive Weather Corridor</h2>
            </div>
            {routes.length > 0 && (
              <div className="map-actions">
                <button
                  type="button"
                  className={'pill-btn ' + (showHeatmap ? 'active' : '')}
                  onClick={() => setShowHeatmap(!showHeatmap)}
                >
                  Heatmap {showHeatmap ? 'ON' : 'OFF'}
                </button>
                <button
                  type="button"
                  className={'pill-btn ' + (showCheckpoints ? 'active' : '')}
                  onClick={() => setShowCheckpoints(!showCheckpoints)}
                >
                  Pins {showCheckpoints ? 'ON' : 'OFF'}
                </button>
              </div>
            )}
          </div>

          <div className="map">
            <MapContainer center={[39.5, -98.35]} zoom={4} scrollWheelZoom>
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />

              <AutoFitBounds activeRoute={active} routes={routes} />

              {/* Weather Heatmap Layer */}
              {showHeatmap && heatmapPoints.length > 0 && (
                <HeatmapLayer points={heatmapPoints} />
              )}

              {/* Route Polylines */}
              {routes.map((r) => {
                const isSel = r.id === active?.id
                return (
                  <Polyline
                    key={r.id}
                    positions={r.geometry.map(([lon, lat]) => [lat, lon])}
                    eventHandlers={{ click: () => setSelected(r.id) }}
                    pathOptions={{
                      color: isSel ? '#16b4ad' : '#4a6974',
                      weight: isSel ? 6 : 3.5,
                      opacity: isSel ? 1.0 : 0.65,
                    }}
                  />
                )
              })}

              {/* Checkpoint Markers with ETAs and Weather Details */}
              {showCheckpoints &&
                active?.checkpoints?.map((cp, i) => {
                  const currentHourEntry = cp.hourly_forecast?.[sliderHour]
                  return (
                    <CircleMarker
                      key={active.id + '-' + i}
                      center={[cp.latitude, cp.longitude]}
                      radius={5.5}
                      pathOptions={{
                        color: '#ffffff',
                        weight: 1.5,
                        fillColor: RISK_COLORS[cp.risk] || '#10b981',
                        fillOpacity: 1,
                      }}
                    >
                      <Popup>
                        <div className="cp-popup">
                          <div className="cp-header" style={{ borderLeftColor: RISK_COLORS[cp.risk] }}>
                            <strong>Mile {cp.distance_miles.toFixed(1)}</strong>
                            <span className={'badge badge-' + cp.risk}>{cp.risk.replace('_', ' ').toUpperCase()}</span>
                          </div>
                          <div className="cp-body">
                            <div>
                              Segment: <b>{cp.segment_miles.toFixed(1)} mi</b>
                            </div>
                            <div>
                              Truck ETA: <b>{new Date(cp.eta).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}</b>
                            </div>
                            <hr />
                            {cp.weather_available === false ? (
                              <div className="popup-alert">
                                ⚠️ Weather data unavailable for this segment (provider timed out or out of range)
                              </div>
                            ) : (
                              <div className="cp-grid">
                                <span>
                                  Wind (ETA): <b>{cp.wind_mph !== null && cp.wind_mph !== undefined ? cp.wind_mph + ' mph' : 'N/A'}</b>
                                </span>
                                <span>
                                  Rain (ETA): <b>{cp.rain_in_hr !== null && cp.rain_in_hr !== undefined ? cp.rain_in_hr + ' in/hr' : 'N/A'}</b>
                                </span>
                                <span>
                                  Snow (ETA): <b>{cp.snow_in_hr !== null && cp.snow_in_hr !== undefined ? cp.snow_in_hr + ' in/hr' : 'N/A'}</b>
                                </span>
                                <span>
                                  Load Risk: <b>{cp.risk ? cp.risk.toUpperCase() : 'UNKNOWN'}</b>
                                </span>
                              </div>
                            )}
                            {cp.station_mapping && (
                              <div className="popup-station-source">
                                <small>Forecast Source:</small>
                                <span>
                                  {cp.station_mapping.is_direct_observation
                                    ? 'Direct corridor weather station'
                                    : `Mapped from nearest corridor station (${cp.station_mapping.distance_to_station_miles} mi away)`}
                                </span>
                              </div>
                            )}
                            {currentHourEntry?.available && (
                              <div className="popup-timeline-info">
                                <small>At Slider (T+{sliderHour}h):</small>
                                <span>Wind: {currentHourEntry.wind_mph} mph · Rain: {currentHourEntry.rain_in_hr} · Snow: {currentHourEntry.snow_in_hr}</span>
                              </div>
                            )}
                            {cp.risk === 'no_travel' && (
                              <div className="popup-alert">⛔ Travel not recommended for this segment</div>
                            )}
                          </div>
                        </div>
                      </Popup>
                    </CircleMarker>
                  )
                })}
            </MapContainer>

            {!routes.length && (
              <div className="empty">Enter your origin, destination, and departure time above to compute risk and display routes.</div>
            )}
          </div>

          {/* 0-48 Hour Forecast Slider & Heatmap Controls */}
          <div className="forecast-controls">
            {!routes.length ? (
              <div className="inactive-prompt">
                <span>ℹ️ Click <b>"ANALYZE ROUTES"</b> above to load routes and activate the 0–48-hour corridor weather heatmap.</span>
              </div>
            ) : (
              <>
                <div className="forecast-header">
                  <div>
                    <label>0–48H CORRIDOR FORECAST HEATMAP</label>
                    <div className="time-display">
                      <b>+{sliderHour}h Forecast Offset</b>
                      <span>({sliderCurrentTime.toUTCString().slice(0, 22)} UTC)</span>
                    </div>
                  </div>
                  <div className="metric-tabs">
                    <button
                      type="button"
                      className={heatMetric === 'risk' ? 'active' : ''}
                      onClick={() => setHeatMetric('risk')}
                    >
                      Risk
                    </button>
                    <button
                      type="button"
                      className={heatMetric === 'wind' ? 'active' : ''}
                      onClick={() => setHeatMetric('wind')}
                    >
                      Wind
                    </button>
                    <button
                      type="button"
                      className={heatMetric === 'rain' ? 'active' : ''}
                      onClick={() => setHeatMetric('rain')}
                    >
                      Rain
                    </button>
                    <button
                      type="button"
                      className={heatMetric === 'snow' ? 'active' : ''}
                      onClick={() => setHeatMetric('snow')}
                    >
                      Snow
                    </button>
                  </div>
                </div>

                {/* Dynamic Metric Scale Legend */}
                <div className="metric-legend">
                  {heatMetric === 'risk' && (
                    <>
                      <span className="legend-chip"><i style={{ background: '#10b981' }} /> Low (0)</span>
                      <span className="legend-chip"><i style={{ background: '#f59e0b' }} /> Moderate (1)</span>
                      <span className="legend-chip"><i style={{ background: '#f97316' }} /> High (2)</span>
                      <span className="legend-chip"><i style={{ background: '#ef4444' }} /> Severe (3)</span>
                      <span className="legend-chip"><i style={{ background: '#b91c1c' }} /> No Travel (4)</span>
                    </>
                  )}
                  {heatMetric === 'wind' && (
                    <>
                      <span className="legend-chip"><i style={{ background: '#0d9488' }} /> &lt; 15 mph (Calm)</span>
                      <span className="legend-chip"><i style={{ background: '#06b6d4' }} /> 15–24 mph</span>
                      <span className="legend-chip"><i style={{ background: '#3b82f6' }} /> 25–34 mph (Mod)</span>
                      <span className="legend-chip"><i style={{ background: '#f59e0b' }} /> 35–44 mph (High)</span>
                      <span className="legend-chip"><i style={{ background: '#f97316' }} /> 45–54 mph (Severe)</span>
                      <span className="legend-chip"><i style={{ background: '#dc2626' }} /> &ge; 55 mph (No Travel)</span>
                    </>
                  )}
                  {heatMetric === 'rain' && (
                    <>
                      <span className="legend-chip"><i style={{ background: '#1e3a45' }} /> 0.00 (Dry Road)</span>
                      <span className="legend-chip"><i style={{ background: '#38bdf8' }} /> &lt; 0.10 in/hr (Light)</span>
                      <span className="legend-chip"><i style={{ background: '#2563eb' }} /> 0.10–0.24 (Mod)</span>
                      <span className="legend-chip"><i style={{ background: '#f59e0b' }} /> 0.25–0.49 (Heavy)</span>
                      <span className="legend-chip"><i style={{ background: '#ef4444' }} /> 0.50–1.00 (Severe)</span>
                      <span className="legend-chip"><i style={{ background: '#7f1d1d' }} /> &gt; 1.00 (No Travel)</span>
                    </>
                  )}
                  {heatMetric === 'snow' && (
                    <>
                      <span className="legend-chip"><i style={{ background: '#2e2744' }} /> 0.00 (Clear Road)</span>
                      <span className="legend-chip"><i style={{ background: '#e0e7ff' }} /> &lt; 0.5 in/hr (Light Ice)</span>
                      <span className="legend-chip"><i style={{ background: '#c084fc' }} /> 0.5–0.9 (Mod Violet)</span>
                      <span className="legend-chip"><i style={{ background: '#9333ea' }} /> 1.0–1.9 (Heavy Purple)</span>
                      <span className="legend-chip"><i style={{ background: '#ec4899' }} /> 2.0–3.0 (Severe Magenta)</span>
                      <span className="legend-chip"><i style={{ background: '#831843' }} /> &gt; 3.0 (No Travel Plum)</span>
                    </>
                  )}
                </div>

                {/* Live Corridor Telemetry Bar */}
                {hasWeather ? (
                  <>
                    <div className="corridor-telemetry">
                      <span>
                        Corridor Peak Wind: <b>{maxCorridorWind.toFixed(1)} mph</b>
                      </span>
                      <span>
                        Corridor Rain:{' '}
                        <b>
                          {maxCorridorRain.toFixed(2)} in/hr
                          {maxCorridorRain === 0 ? ' (Dry)' : ''}
                        </b>
                      </span>
                      <span>
                        Corridor Snow:{' '}
                        <b>
                          {maxCorridorSnow.toFixed(2)} in/hr
                          {maxCorridorSnow === 0 ? ' (Clear)' : ''}
                        </b>
                      </span>
                      <span>
                        Corridor Status:{' '}
                        <b style={{ color: RISK_COLORS[worstCorridorRisk] || '#10b981' }}>
                          {worstCorridorRisk.replace('_', ' ').toUpperCase()}
                        </b>
                      </span>
                    </div>
                    {heatMetric === 'risk' && worstCorridorRisk === 'low' && (
                      <div className="corridor-hint">
                        💡 <b>Calm conditions:</b> Risk is LOW throughout this 48h corridor. Switch metric to <b>Wind</b> above to observe active wind variations along the route.
                      </div>
                    )}
                    {heatMetric === 'rain' && maxCorridorRain === 0 && (
                      <div className="corridor-hint">
                        💡 <b>Dry road:</b> Zero rain across the corridor for this hour. Route corridor rendered in neutral dry slate (0.00 in/hr).
                      </div>
                    )}
                    {heatMetric === 'snow' && maxCorridorSnow === 0 && (
                      <div className="corridor-hint">
                        💡 <b>Clear road:</b> Zero snowfall across the corridor for this hour. Route corridor rendered in neutral clear slate (0.00 in/hr).
                      </div>
                    )}
                  </>
                ) : (
                  <div className="missing-data-note">
                    ⚠️ Live weather service is currently overloaded by provider. Route geometry & ETAs are active.
                  </div>
                )}

                <div className="slider-row">
                  <button
                    type="button"
                    className="play-btn"
                    disabled={!hasWeather}
                    onClick={() => setIsPlaying(!isPlaying)}
                    title={isPlaying ? 'Pause animation' : 'Animate forecast'}
                  >
                    {isPlaying ? '⏸ Pause' : '▶ Animate'}
                  </button>
                  <input
                    type="range"
                    min="0"
                    max="48"
                    step="1"
                    value={sliderHour}
                    disabled={!hasWeather}
                    onChange={(e) => {
                      setIsPlaying(false)
                      setSliderHour(Number(e.target.value))
                    }}
                  />
                  <span className="hour-badge">T+{sliderHour}h</span>
                </div>
              </>
            )}
          </div>
        </section>
      </section>

      {/* Route Comparison Section */}
      <section className="panel results">
        <label>03 / ROUTE EVALUATION & ALTERNATIVES</label>
        <h2>Recommendation & Metrics</h2>

        {data?.warning && <div className="warning-box">ℹ️ {data.warning}</div>}

        {!routes.length ? (
          <p className="no-routes">Route comparison and safety metrics will appear here after analysis.</p>
        ) : (
          <div className="cards">
            {routes.map((r) => {
              const isSel = r.id === active?.id
              return (
                <button
                  type="button"
                  className={'card ' + (r.recommended ? 'best ' : '') + (isSel ? 'chosen ' : '') + (r.has_no_travel ? 'has-no-travel' : '')}
                  key={r.id}
                  onClick={() => setSelected(r.id)}
                >
                  <div className="card-top">
                    <label>
                      OPTION 0{r.rank} {r.recommended ? '· BEST MATCH' : ''}
                    </label>
                    {r.has_no_travel && <span className="no-travel-badge">⛔ NO TRAVEL ADVISORY</span>}
                  </div>
                  <h3>{r.name}</h3>

                  <div className="metrics">
                    <span>
                      Distance
                      <strong>{Math.round(r.distance_miles).toLocaleString()} mi</strong>
                    </span>
                    <span>
                      Drive Time
                      <strong>{(r.duration_seconds / 3600).toFixed(1)} hr</strong>
                    </span>
                  </div>

                  <div className="risk-metrics">
                    {r.weather_available === false ? (
                      <div className="unavailable-notice" style={{ color: '#e2b36f', fontSize: '11px', padding: '6px 0' }}>
                        ⚠️ <b>Weather Unavailable:</b> Risk cannot be verified (provider outage).
                      </div>
                    ) : (
                      <>
                        <p>
                          Severe miles <b>{r.severe_miles != null ? r.severe_miles.toFixed(1) + ' mi' : 'N/A'}</b>
                        </p>
                        <p>
                          High miles <b>{r.high_miles != null ? r.high_miles.toFixed(1) + ' mi' : 'N/A'}</b>
                        </p>
                        <p>
                          No Travel miles <b>{r.no_travel_miles != null ? r.no_travel_miles.toFixed(1) + ' mi' : 'N/A'}</b>
                        </p>
                        <p>
                          Average risk <b>{r.average_risk != null ? r.average_risk.toFixed(2) : 'N/A'}</b>
                        </p>
                      </>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </section>

      <footer>
        SPOTTER / ROUTE INTELLIGENCE &bull; Real-time weather-aware routing &bull; Powered by OSRM & Open-Meteo APIs
      </footer>
    </main>
  )
}
