// controlApi.ts

const API_BASE = '/control/mcp';

export interface OverviewStats {
  total_clients: number;
  active_connections: number;
  pending_approvals: number;
  activity_summary: Record<string, number>;
}

export const fetchOverview = async (): Promise<OverviewStats> => {
  const res = await fetch(`${API_BASE}/overview`);
  if (!res.ok) throw new Error('Failed to fetch overview');
  return res.json();
};

export const fetchClients = async () => {
  const res = await fetch(`${API_BASE}/clients`); 
  return res.json();
};

export const fetchConnections = async () => {
  const res = await fetch(`${API_BASE}/connections`); 
  return res.json();
};

export const fetchActivity = async (offset = 0, limit = 50) => {
  const res = await fetch(`${API_BASE}/activity?offset=${offset}&limit=${limit}`);
  return res.json();
};

export const fetchPendingApprovals = async () => {
  const res = await fetch(`${API_BASE}/approvals/pending`);
  return res.json();
};

export const decideApproval = async (approvalId: string, decision: 'APPROVE' | 'DENY', reason?: string) => {
  const res = await fetch(`${API_BASE}/approvals/${approvalId}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, reason })
  });
  return res.json();
};

