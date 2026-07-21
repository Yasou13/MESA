# Cross-system check

Authentication: server and alternate script attach configured principal only after valid API key; live server import/runtime was not attempted because dotenv isolation remains blocked.
Authorization: target route fail-closes for an active principal without explicit `SESSION_CREATE`.
Session ownership: `start_session` is covered; principal ownership checks for context/end/status/purge remain unverified follow-up scope.
SDK/MCP: no `/session/start` caller test was found; async header drift and MCP tool identity remain open canonical work.
Worker/config/compatibility: no provider, Docker, worker, real `.env`, or runtime server was used.
