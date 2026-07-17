#!/bin/bash
export MESA_API_KEY="test_key"
export HF_HUB_OFFLINE="1"
export MESA_DAILY_REQUEST_LIMIT="100"

# Start server in background
PYTHONPATH=. venv/bin/python -m uvicorn mesa_memory.api.server:app --port 8100 --host 127.0.0.1 > uvicorn.log 2>&1 &
SERVER_PID=$!

sleep 5

# Start session
SESSION_RESP=$(curl -s -X POST http://127.0.0.1:8100/v3/memory/session/start \
  -H "X-API-Key: test_key" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "manual-agent"}')
echo "Session: $SESSION_RESP"

SESSION_ID=$(echo $SESSION_RESP | grep -o '"session_id":"[^"]*' | grep -o '[^"]*$')

# Insert memory
curl -s -X POST http://127.0.0.1:8100/v3/memory/insert \
  -H "X-API-Key: test_key" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "manual-agent", "session_id": "'"$SESSION_ID"'", "content": "Manual test content"}'
echo "\nInsert done."

sleep 2

# Check Uvicorn log
cat uvicorn.log

kill $SERVER_PID
