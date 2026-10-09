import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/maplibre';
import { GeoJsonLayer } from '@deck.gl/layers';

export interface ClashSummary {
  ticket_id: string;
  total_utilities_evaluated: number;
  total_clashes: number;
  quality_breakdown: Record<string, number>;
  statutory_tolerance_in: number;
  tolerance_buffer_m: number;
}

export interface MapViewProps {
  onClashUpdate?: (summary: ClashSummary | null) => void;
}

export const MapView: React.FC<MapViewProps> = ({ onClashUpdate }) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);

  const [ticketPolygon, setTicketPolygon] = useState<any>(null);
  const [clashResult, setClashResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [stateCode, setStateCode] = useState<'TX' | 'CA'>('TX');
  const [exportSrid, setExportSrid] = useState<number | null>(null);

  // Initialize MapLibre GL JS v4 with interleaved deck.gl v9 WebGL overlay
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    // 1. Initialize Map from maplibre-gl
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
      center: [-97.7431, 30.2672], // Austin, TX
      zoom: 15.5,
      pitch: 55,
      bearing: -20,
      antialias: true
    });

    // 2. Instantiate MapboxOverlay with interleaved: true for shared WebGL2 context
    const overlay = new MapboxOverlay({
      interleaved: true,
      layers: []
    });

    // 3. Bind overlay to map instance
    map.addControl(overlay);

    mapRef.current = map;
    overlayRef.current = overlay;

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, []);

  // Update Deck.gl layers whenever data or state changes
  useEffect(() => {
    if (!overlayRef.current) return;

    const layers = [
      // 1. Base SUE Utility Features Layer
      new GeoJsonLayer({
        id: 'utility-lines',
        data: exportSrid ? `/api/v1/utilities?export_srid=${exportSrid}` : '/api/v1/utilities',
        pickable: true,
        stroked: true,
        filled: true,
        lineWidthUnits: 'meters',
        getLineWidth: (f: any) => {
          const diaMm = f.properties?.nominal_diameter_mm || 150;
          return Math.max(0.4, diaMm / 500);
        },
        getLineColor: (f: any) => {
          // Color-coded by ASCE 38-22 Quality Level
          const ql = f.properties?.quality_level;
          switch (ql) {
            case 'QL-A':
              return [46, 204, 113, 230]; // Bright Green (Physically verified 3D)
            case 'QL-B':
              return [241, 196, 15, 230]; // Yellow (Geophysical surface designate)
            case 'QL-C':
              return [230, 126, 34, 230]; // Orange (Surface appurtenances)
            case 'QL-D':
              return [231, 76, 60, 230]; // Red (Unverified records)
            default:
              return [52, 152, 219, 200];
          }
        },
        getFillColor: [100, 100, 100, 150],
        pointRadiusMinPixels: 6,
        getPointRadius: 8
      }),

      // 2. Active 811 Excavation Ticket Polygon (3D Extrusion)
      ticketPolygon &&
        new GeoJsonLayer({
          id: '811-ticket-prism',
          data: ticketPolygon,
          pickable: true,
          filled: true,
          stroked: true,
          extruded: true,
          getElevation: 15, // Visualized 3D excavation prism
          getFillColor: [231, 76, 60, 70],
          getLineColor: [192, 57, 43, 255],
          getLineWidth: 2,
          lineWidthUnits: 'pixels'
        }),

      // 3. Highlight Clashing Assets within Statutory Tolerance Zone
      clashResult?.geojson &&
        new GeoJsonLayer({
          id: 'clash-detection-overlay',
          data: clashResult.geojson,
          pickable: true,
          lineWidthUnits: 'meters',
          getLineWidth: (f: any) => (f.properties?.is_clash ? 1.2 : 0.4),
          getLineColor: (f: any) => {
            if (f.properties?.clash_reason === 'COLLISION') {
              return [255, 0, 0, 255]; // Direct 3D Collision
            }
            if (f.properties?.clash_reason === 'WITHIN_TOLERANCE_ZONE') {
              return [255, 140, 0, 255]; // Within Vacuum Excavation Tolerance Zone
            }
            return [150, 150, 150, 80];
          }
        })
    ].filter(Boolean);

    overlayRef.current.setProps({ layers });
  }, [ticketPolygon, clashResult, exportSrid]);

  // Execute 3D Collision Detection against 811 Ticket
  const run811ClashDetection = async () => {
    setLoading(true);
    const demoTicket = {
      ticket_id: `811-AUSTIN-${Math.floor(Math.random() * 9000 + 1000)}`,
      state_code: stateCode,
      polygon: {
        type: 'Polygon',
        coordinates: [
          [
            [-97.7442, 30.2663],
            [-97.7418, 30.2663],
            [-97.7418, 30.2682],
            [-97.7442, 30.2682],
            [-97.7442, 30.2663]
          ]
        ]
      },
      source_srid: 4326,
      ground_elevation_m: 0.0,
      excavation_depth_m: 2.5,
      work_type: 'Directional Boring & Trenching',
      excavator: 'Lone Star Infrastructure LLC'
    };

    setTicketPolygon(demoTicket.polygon);

    try {
      const res = await fetch('/api/v1/clash-detect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticket: demoTicket,
          export_srid: exportSrid
        })
      });
      if (res.ok) {
        const data = await res.json();
        setClashResult(data);
        if (onClashUpdate) {
          onClashUpdate(data.summary);
        }
      }
    } catch (err) {
      console.error('Failed to run clash detection:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* MapLibre WebGL container */}
      <div ref={mapContainerRef} style={{ width: '100%', height: '100%' }} />

      {/* Control Overlay Panel */}
      <div
        style={{
          position: 'absolute',
          top: 20,
          left: 20,
          background: 'rgba(255, 255, 255, 0.95)',
          padding: '18px 22px',
          borderRadius: 8,
          boxShadow: '0 4px 18px rgba(0, 0, 0, 0.15)',
          maxWidth: 380,
          zIndex: 10,
          fontSize: 13,
          lineHeight: 1.5
        }}
      >
        <h3 style={{ margin: '0 0 10px 0', fontSize: 16, color: '#2c3e50' }}>
          SUE 3D Platform | 811 Ticket Automation
        </h3>
        <p style={{ margin: '0 0 12px 0', color: '#7f8c8d' }}>
          ASCE 38-22 / ASCE 75-22 Compliance & Vacuum Excavation Clearance
        </p>

        <div style={{ marginBottom: 12 }}>
          <strong>Statutory Jurisdiction:</strong>
          <div style={{ marginTop: 4 }}>
            <label style={{ marginRight: 15, cursor: 'pointer' }}>
              <input
                type="radio"
                name="state"
                value="TX"
                checked={stateCode === 'TX'}
                onChange={() => setStateCode('TX')}
              />{' '}
              Texas (18" Buffer)
            </label>
            <label style={{ cursor: 'pointer' }}>
              <input
                type="radio"
                name="state"
                value="CA"
                checked={stateCode === 'CA'}
                onChange={() => setStateCode('CA')}
              />{' '}
              California (24" Buffer)
            </label>
          </div>
        </div>

        <div style={{ marginBottom: 14 }}>
          <strong>Coordinate Export:</strong>
          <div style={{ marginTop: 4 }}>
            <select
              value={exportSrid || ''}
              onChange={(e) => setExportSrid(e.target.value ? Number(e.target.value) : null)}
              style={{ padding: '4px 8px', width: '100%' }}
            >
              <option value="">WGS84 (EPSG:4326) - Storage Default</option>
              <option value="6578">NAD83(2011) / Texas Central ftUS (EPSG:6578)</option>
              <option value="2277">NAD83 / Texas Central ftUS (EPSG:2277)</option>
            </select>
          </div>
        </div>

        <button
          onClick={run811ClashDetection}
          disabled={loading}
          style={{
            width: '100%',
            background: loading ? '#95a5a6' : '#2980b9',
            color: '#fff',
            border: 'none',
            padding: '10px 14px',
            borderRadius: 6,
            fontWeight: 600,
            cursor: loading ? 'not-allowed' : 'pointer'
          }}
        >
          {loading ? 'Evaluating 3D Clash...' : 'Simulate 811 Dig Ticket'}
        </button>

        {/* Legend */}
        <div style={{ marginTop: 15, paddingTop: 10, borderTop: '1px solid #eee' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>ASCE 38-22 Quality Levels:</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
            <div><span style={{ color: '#2ecc71' }}>●</span> QL-A (≤0.03m 3D)</div>
            <div><span style={{ color: '#f1c40f' }}>●</span> QL-B (≤0.06m 2D)</div>
            <div><span style={{ color: '#e67e22' }}>●</span> QL-C (Surveyed)</div>
            <div><span style={{ color: '#e74c3c' }}>●</span> QL-D (Records)</div>
          </div>
        </div>

        {clashResult && (
          <div style={{ marginTop: 15, paddingTop: 10, borderTop: '1px solid #eee' }}>
            <div style={{ fontWeight: 600, color: '#c0392b' }}>
              Clashes Detected: {clashResult.summary.total_clashes}
            </div>
            <div style={{ color: '#555', fontSize: 12 }}>
              Tolerance Buffer: {clashResult.summary.tolerance_buffer_m}m ({clashResult.summary.statutory_tolerance_in}")
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
