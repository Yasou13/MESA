import React, { useEffect, useState } from 'react';
import { ActivitySquare, Users, Network, CheckSquare } from 'lucide-react';
import { fetchOverview, type OverviewStats } from '../api/controlApi';

export const Overview: React.FC = () => {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchOverview()
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex gap-6" style={{ flexWrap: 'wrap' }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="glass-panel skeleton" style={{ width: '240px', height: '120px' }} />
        ))}
      </div>
    );
  }

  const statCards = [
    { label: 'Total Clients', value: stats?.total_clients || 0, icon: Users, color: 'var(--accent-primary)' },
    { label: 'Active Connections', value: stats?.active_connections || 0, icon: Network, color: 'var(--success)' },
    { label: 'Pending Approvals', value: stats?.pending_approvals || 0, icon: CheckSquare, color: 'var(--warning)' },
    { label: 'Recent Activity', value: Object.values(stats?.activity_summary || {}).reduce((a, b) => a + b, 0), icon: ActivitySquare, color: 'var(--text-primary)' },
  ];

  return (
    <div>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Control Center Overview</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Real-time metrics and system health for MESA Control Plane.</p>
      </div>

      <div className="flex gap-6" style={{ flexWrap: 'wrap', marginBottom: '32px' }}>
        {statCards.map((stat, i) => (
          <div key={i} className="glass-panel" style={{ padding: '24px', flex: '1 1 200px', display: 'flex', alignItems: 'center', gap: '16px' }}>
            <div style={{ background: 'rgba(255,255,255,0.05)', padding: '12px', borderRadius: 'var(--radius-md)' }}>
              <stat.icon size={24} color={stat.color} />
            </div>
            <div>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', fontWeight: 500, marginBottom: '4px' }}>{stat.label}</p>
              <h2 style={{ fontSize: '1.75rem', margin: 0 }}>{stat.value}</h2>
            </div>
          </div>
        ))}
      </div>
      
      <div className="glass-panel" style={{ padding: '24px', minHeight: '300px' }}>
        <h3 style={{ marginBottom: '20px' }}>Activity Summary</h3>
        {stats?.activity_summary && Object.keys(stats.activity_summary).length > 0 ? (
          <div className="flex flex-col gap-4">
            {Object.entries(stats.activity_summary).map(([status, count]) => (
              <div key={status} className="flex items-center justify-between" style={{ padding: '12px 16px', background: 'rgba(255,255,255,0.02)', borderRadius: 'var(--radius-sm)' }}>
                <span className="flex items-center gap-2">
                  <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: status === 'SUCCESS' ? 'var(--success)' : status === 'DENIED' ? 'var(--error)' : 'var(--accent-primary)' }} />
                  {status}
                </span>
                <span style={{ fontWeight: 600 }}>{count}</span>
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: 'var(--text-muted)', textAlign: 'center', marginTop: '60px' }}>No activity data available.</p>
        )}
      </div>
    </div>
  );
};
