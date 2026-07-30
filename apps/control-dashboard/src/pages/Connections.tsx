import React from 'react';

export const Connections: React.FC = () => {
  return (
    <div>
      <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Active Connections</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '32px' }}>Monitor and manage live MCP connections.</p>
      <div className="glass-panel" style={{ padding: '24px' }}>
        <p style={{ color: 'var(--text-muted)' }}>Connection monitoring coming soon...</p>
      </div>
    </div>
  );
};
