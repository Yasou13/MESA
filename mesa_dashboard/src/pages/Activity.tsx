import React, { useEffect, useState } from 'react';
import { fetchActivity } from '../api/controlApi';

export const Activity: React.FC = () => {
  const [activities, setActivities] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchActivity()
      .then(res => setActivities(res.items || []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Activity Log</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Immutable audit trail of all MCP tool executions.</p>
      </div>

      <div className="glass-panel" style={{ overflowX: 'auto' }}>
        {loading ? (
          <div style={{ padding: '24px' }}>Loading activity...</div>
        ) : error ? (
          <div style={{ padding: '24px', color: 'var(--error)' }}>Error: {error}</div>
        ) : activities.length === 0 ? (
          <div style={{ padding: '24px', color: 'var(--text-muted)' }}>No activities recorded yet.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Client</th>
                <th>Tool</th>
                <th>Status</th>
                <th>Duration (ms)</th>
              </tr>
            </thead>
            <tbody>
              {activities.map((act) => (
                <tr key={act.call_id}>
                  <td>{new Date(act.started_at).toLocaleString()}</td>
                  <td>{act.client_id}</td>
                  <td><span className="badge badge-warning" style={{ background: 'rgba(255,255,255,0.05)', color: 'var(--text-primary)' }}>{act.tool_name}</span></td>
                  <td>
                    <span className={`badge badge-${act.status === 'SUCCESS' ? 'success' : act.status === 'DENIED' ? 'error' : 'warning'}`}>
                      {act.status}
                    </span>
                  </td>
                  <td>{act.duration_ms || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
