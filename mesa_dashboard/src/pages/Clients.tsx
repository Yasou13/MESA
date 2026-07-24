import React from 'react';

export const Clients: React.FC = () => {
  return (
    <div>
      <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Clients & Bindings</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '32px' }}>Manage connected agents and their dataset bindings.</p>
      <div className="glass-panel" style={{ padding: '24px' }}>
        <p style={{ color: 'var(--text-muted)' }}>Client management coming soon...</p>
      </div>
    </div>
  );
};
