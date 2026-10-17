import { useEffect, useRef } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'

/*
 * WeatherCorridorHeatmap
 * Unlike standard density heatmaps (which turn red simply when points are close together),
 * a weather heatmap represents physical scalar values (Risk, Wind, Rain, Snow).
 *
 * Each point radiates its TRUE weather color (e.g., green for Low, amber for Moderate,
 * red for Severe) with a smooth radial blur that blends seamlessly along the highway corridor
 * without artificially accumulating into high-risk red.
 */

function hexToRgb(hex) {
  const clean = hex.replace('#', '')
  const num = parseInt(clean, 16)
  return {
    r: (num >> 16) & 255,
    g: (num >> 8) & 255,
    b: num & 255,
  }
}

export default function HeatmapLayer({ points, zoomScale = true }) {
  const map = useMap()
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = L.DomUtil.create('canvas', 'leaflet-weather-heat-layer leaflet-layer')
    const size = map.getSize()
    canvas.width = size.x
    canvas.height = size.y
    canvas.style.position = 'absolute'
    canvas.style.pointerEvents = 'none'
    canvas.style.zIndex = '340'

    const pane = map.getPanes().overlayPane
    pane.appendChild(canvas)
    canvasRef.current = canvas

    function updatePositionAndSize() {
      if (!canvasRef.current) return
      const topLeft = map.containerPointToLayerPoint([0, 0])
      L.DomUtil.setPosition(canvasRef.current, topLeft)
      const currentSize = map.getSize()
      if (canvasRef.current.width !== currentSize.x || canvasRef.current.height !== currentSize.y) {
        canvasRef.current.width = currentSize.x
        canvasRef.current.height = currentSize.y
      }
      draw()
    }

    function draw() {
      if (!canvasRef.current) return
      const ctx = canvasRef.current.getContext('2d')
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)

      if (!points || !points.length) return

      const zoom = map.getZoom()
      // Scale radius smoothly with map zoom so corridor width remains natural
      const radius = Math.max(16, Math.min(55, Math.round(18 * Math.pow(1.22, zoom - 4))))

      // Draw each checkpoint's weather glow
      // p: [lat, lon, colorHex, opacity]
      for (let i = 0; i < points.length; i++) {
        const p = points[i]
        const latLng = [p[0], p[1]]
        const pt = map.latLngToContainerPoint(latLng)

        const colorHex = p[2] || '#10b981'
        const baseOpacity = p[3] !== undefined ? p[3] : 0.65
        const { r, g, b } = hexToRgb(colorHex)

        const gradient = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, radius)
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${baseOpacity})`)
        gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${baseOpacity * 0.6})`)
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`)

        ctx.fillStyle = gradient
        ctx.beginPath()
        ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    map.on('move', updatePositionAndSize)
    map.on('moveend', updatePositionAndSize)
    map.on('zoomend', updatePositionAndSize)
    updatePositionAndSize()

    return () => {
      map.off('move', updatePositionAndSize)
      map.off('moveend', updatePositionAndSize)
      map.off('zoomend', updatePositionAndSize)
      if (canvasRef.current && pane.contains(canvasRef.current)) {
        pane.removeChild(canvasRef.current)
      }
    }
  }, [map])

  // Redraw when points change (e.g. timeline slider moves or metric changes)
  useEffect(() => {
    if (!canvasRef.current) return
    const ctx = canvasRef.current.getContext('2d')
    ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height)

    if (!points || !points.length) return

    const zoom = map.getZoom()
    const radius = Math.max(16, Math.min(55, Math.round(18 * Math.pow(1.22, zoom - 4))))

    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      const latLng = [p[0], p[1]]
      const pt = map.latLngToContainerPoint(latLng)

      const colorHex = p[2] || '#10b981'
      const baseOpacity = p[3] !== undefined ? p[3] : 0.65
      const { r, g, b } = hexToRgb(colorHex)

      const gradient = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, radius)
      gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${baseOpacity})`)
      gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${baseOpacity * 0.6})`)
      gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`)

      ctx.fillStyle = gradient
      ctx.beginPath()
      ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2)
      ctx.fill()
    }
  }, [points, map])

  return null
}
