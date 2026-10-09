import React, { useState } from 'react';
import { MapView, ClashSummary } from './MapView';

export const App: React.FC = () => {
  const [summary, setSummary] = useState<ClashSummary | null>(null);

  return (
    <div style={{ width: '100vw', height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header
        style={{
          height: 48,
          background: '#1a252f',
          color: '#ecf0f1',
          display: 'flex',
          alignItems: 'center',
          padding: '0 20px',
          justifyContent: 'space-between',
          zIndex: 20,
          boxShadow: '0 2px 8px rgba(0,0,0,0.2)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: 0.5 }}>
            SUE GEOMATICS CLOUD
          </span>
          <span style={{ fontSize: 12, background: '#34495e', padding: '2px 8px', borderRadius: 4 }}>
            ASCE 38-22 / 75-22 Certified
          </span>
        </div>
        {summary && (
          <div style={{ fontSize: 13, display: 'flex', gap: 16 }}>
            <span>Ticket: <strong>{summary.ticket_id}</strong></span>
            <span>Evaluated: <strong>{summary.total_utilities_evaluated}</strong></span>
            <span style={{ color: summary.total_clashes > 0 ? '#e74c3c' : '#2ecc71', fontWeight: 600 }}>
              Clashes: {summary.total_clashes}
            </span>
          </div>
        )}
      </header>
      <main style={{ flex: 1, position: 'relative' }}>
        <MapView onClashUpdate={setSummary} />
      </main>
    </div>
  );
};
