// controlApi.ts

const API_BASE = '/control/mcp';

export interface OverviewStats {
  total_clients: number;
  active_clients: number;
  connections_by_status: Record<string, number>;
  pending_approvals: number;
  calls_by_status: Record<string, number>;
}

export interface CodexCredential {
  credential_id: string;
  token_prefix: string;
  status: 'ACTIVE' | 'REVOKED';
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

export interface CodexBinding {
  binding: { binding_id: string; external_project_id: string; tenant_id: string; workspace_id: string; dataset_id: string; enabled: boolean };
  profile: { max_records: number; max_tokens: number; memory_types: string[]; revision: number };
  credentials: CodexCredential[];
  active_connections: number;
  pending_approvals: number;
}

export interface CodexClient {
  client: { client_id: string; display_name: string; client_type: string; enabled: boolean };
  bindings: CodexBinding[];
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

export const fetchCodexClients = async (): Promise<{ clients: CodexClient[] }> => {
  const res = await fetch(`${API_BASE}/managed-clients`);
  if (!res.ok) throw new Error('Failed to fetch managed MCP clients');
  return res.json();
};

export const revokeCodexCredential = async (credentialId: string) => {
  const res = await fetch(`${API_BASE}/credentials/${encodeURIComponent(credentialId)}/revoke`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to revoke Codex credential');
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
    body: JSON.stringify({ status: decision === 'APPROVE' ? 'APPROVED' : 'REJECTED', decided_by: 'dashboard', reason })
  });
  return res.json();
};
