# Root cause

The original SEC-002 path authenticated a shared API key without producing a server-side principal, then allowed `start_session` to grant WRITE for client-supplied `agent_id`. Current uncommitted source contains a principal context, explicit `principal_agent_permissions`, and a `SESSION_CREATE` check before grant. Clean restart verified the current target behavior, but did not observe the historical 200 response because the patch was already present and hash-matched.
