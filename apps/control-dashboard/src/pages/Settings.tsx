import React from 'react';
import { ShieldAlert, Activity, GitBranch } from 'lucide-react';

export const Settings: React.FC = () => {
  return (
    <div>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Security & Operations</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Advanced operations, anomaly detection, and graph exploration.</p>
      </div>

      <div className="flex gap-6" style={{ flexWrap: 'wrap' }}>
        <div className="glass-panel" style={{ flex: '1 1 300px', padding: '24px' }}>
          <div className="flex items-center gap-3" style={{ marginBottom: '16px' }}>
            <div style={{ background: 'rgba(255,100,100,0.1)', padding: '10px', borderRadius: '8px' }}>
              <ShieldAlert size={20} color="var(--error)" />
            </div>
            <h2 style={{ fontSize: '1.25rem' }}>Anomaly Detection</h2>
          </div>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', fontSize: '0.9rem' }}>
            Monitor unusual API usage patterns, large payloads, or sudden spikes in tool calls.
          </p>
          <div style={{ padding: '16px', background: 'rgba(255,255,255,0.02)', borderRadius: 'var(--radius-sm)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Status: Scanning active</p>
            <div style={{ marginTop: '12px', height: '4px', background: 'var(--border)', borderRadius: '2px', overflow: 'hidden' }}>
              <div style={{ width: '100%', height: '100%', background: 'var(--success)' }} />
            </div>
          </div>
        </div>

        <div className="glass-panel" style={{ flex: '1 1 300px', padding: '24px' }}>
          <div className="flex items-center gap-3" style={{ marginBottom: '16px' }}>
            <div style={{ background: 'rgba(100,200,255,0.1)', padding: '10px', borderRadius: '8px' }}>
              <Activity size={20} color="var(--accent-primary)" />
            </div>
            <h2 style={{ fontSize: '1.25rem' }}>Latency Tracing</h2>
          </div>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', fontSize: '0.9rem' }}>
            End-to-end tracing for MCP tools. Monitor middleware and engine performance.
          </p>
          <button className="btn btn-primary" style={{ width: '100%' }}>View Trace Metrics</button>
        </div>

        <div className="glass-panel" style={{ flex: '1 1 300px', padding: '24px' }}>
          <div className="flex items-center gap-3" style={{ marginBottom: '16px' }}>
            <div style={{ background: 'rgba(150,100,255,0.1)', padding: '10px', borderRadius: '8px' }}>
              <GitBranch size={20} color="#9b59b6" />
            </div>
            <h2 style={{ fontSize: '1.25rem' }}>Graph Explorer</h2>
          </div>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', fontSize: '0.9rem' }}>
            Interactive node-edge visualization of MESA Memory Graph (KùzuDB).
          </p>
          <button className="btn btn-secondary" style={{ width: '100%' }}>Launch Explorer</button>
        </div>
      </div>
    </div>
  );
};
