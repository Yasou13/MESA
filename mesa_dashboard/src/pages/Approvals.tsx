import React, { useEffect, useState } from 'react';
import { fetchPendingApprovals, decideApproval } from '../api/controlApi';
import { Check, X } from 'lucide-react';

export const Approvals: React.FC = () => {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadApprovals = () => {
    setLoading(true);
    fetchPendingApprovals()
      .then(res => setApprovals(res.items || []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadApprovals();
  }, []);

  const handleDecision = async (id: string, decision: 'APPROVE' | 'DENY') => {
    try {
      await decideApproval(id, decision, 'Manual dashboard decision');
      loadApprovals(); // reload
    } catch (err: any) {
      alert(`Failed to submit decision: ${err.message}`);
    }
  };

  return (
    <div>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Pending Approvals</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Review and authorize high-risk operations.</p>
      </div>

      <div className="flex flex-col gap-4">
        {loading ? (
          <div className="glass-panel skeleton" style={{ height: '100px' }} />
        ) : error ? (
          <div className="glass-panel" style={{ padding: '24px', color: 'var(--error)' }}>Error: {error}</div>
        ) : approvals.length === 0 ? (
          <div className="glass-panel flex flex-col items-center justify-center" style={{ padding: '48px 24px', color: 'var(--text-muted)' }}>
            <Check size={48} style={{ opacity: 0.2, marginBottom: '16px' }} />
            <p>No pending approvals. You're all caught up!</p>
          </div>
        ) : (
          approvals.map((req) => (
            <div key={req.approval_id} className="glass-panel flex justify-between items-center" style={{ padding: '24px' }}>
              <div>
                <div className="flex items-center gap-2" style={{ marginBottom: '8px' }}>
                  <span className="badge badge-warning">{req.operation}</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>Client: {req.client_id}</span>
                </div>
                <p style={{ fontWeight: 500, fontSize: '1.1rem', marginBottom: '4px' }}>{req.request_summary}</p>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>Requested at {new Date(req.requested_at).toLocaleString()}</p>
              </div>
              <div className="flex gap-2">
                <button className="btn btn-secondary" onClick={() => handleDecision(req.approval_id, 'DENY')} style={{ color: 'var(--error)' }}>
                  <X size={16} style={{ marginRight: '6px' }} /> Deny
                </button>
                <button className="btn btn-primary" onClick={() => handleDecision(req.approval_id, 'APPROVE')}>
                  <Check size={16} style={{ marginRight: '6px' }} /> Approve
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
