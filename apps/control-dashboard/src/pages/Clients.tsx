import React, { useEffect, useState } from 'react';
import { fetchCodexClients, revokeCodexCredential, type CodexClient } from '../api/controlApi';

export const Clients: React.FC = () => {
  const [clients, setClients] = useState<CodexClient[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => fetchCodexClients().then(result => setClients(result.clients)).catch(() => setError('Codex clients could not be loaded.'));
  useEffect(() => { void refresh(); }, []);
  const revoke = async (credentialId: string) => {
    if (!window.confirm('Revoke this Codex credential? Existing sessions will fail on their next call.')) return;
    try {
      await revokeCodexCredential(credentialId);
      await refresh();
    } catch {
      setError('Credential could not be revoked.');
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>Clients & Bindings</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '32px' }}>Manage connected agents and their dataset bindings.</p>
      {error && <p style={{ color: 'var(--error)', marginBottom: '16px' }}>{error}</p>}
      {clients.length === 0 ? <div className="glass-panel" style={{ padding: '24px' }}><p style={{ color: 'var(--text-muted)' }}>No managed MCP bindings are installed.</p></div> : clients.map(({ client, bindings }) => (
        <section key={client.client_id} className="glass-panel" style={{ padding: '24px', marginBottom: '16px' }}>
          <h2 style={{ marginBottom: '8px' }}>{client.display_name}</h2>
          <p style={{ color: 'var(--text-muted)', marginBottom: '16px' }}>{client.client_type} · {client.client_id}</p>
          {bindings.map(({ binding, profile, credentials, active_connections, pending_approvals }) => (
            <div key={binding.binding_id} style={{ borderTop: '1px solid var(--border)', paddingTop: '16px', marginTop: '16px' }}>
              <p><strong>Workspace:</strong> {binding.external_project_id.slice(0, 16)}… · {active_connections} active · {pending_approvals} pending approvals</p>
              <p style={{ color: 'var(--text-secondary)' }}>Context: {profile.max_records} records / {profile.max_tokens} tokens · {profile.memory_types.join(', ')}</p>
              {credentials.map(credential => <div key={credential.credential_id} className="flex items-center justify-between" style={{ marginTop: '12px' }}>
                <span style={{ color: 'var(--text-muted)' }}>{credential.token_prefix}… · {credential.status}{credential.last_used_at ? ` · last used ${credential.last_used_at}` : ''}</span>
                {credential.status === 'ACTIVE' && <button onClick={() => void revoke(credential.credential_id)}>Revoke</button>}
              </div>)}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
};
