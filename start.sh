#!/bin/bash
# Wall Street Bro — Railway startup
# FastAPI: port 8000 (internal) — starts first, Streamlit waits for it
# Agent loop: background — isolated, crashes don't affect dashboard
# Streamlit: $PORT (Railway's public port, foreground)

export FASTAPI_PORT=8000
export API_BASE_URL="http://localhost:8000"

# Start FastAPI first — log output so Railway captures any crash reason
echo "[start.sh] Starting FastAPI on port 8000..."
python main.py --mode api &
FASTAPI_PID=$!

# Start agent loop separately
echo "[start.sh] Starting agent loop..."
python main.py --mode agent &

# Wait until FastAPI is healthy before starting Streamlit
# This prevents "Connection refused" on first page load
echo "[start.sh] Waiting for FastAPI to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "[start.sh] FastAPI ready after ${i}s"
        break
    fi
    if ! kill -0 $FASTAPI_PID 2>/dev/null; then
        echo "[start.sh] ERROR: FastAPI process died (PID $FASTAPI_PID). Check logs above."
        # Still start Streamlit so the container stays alive for debugging
        break
    fi
    echo "[start.sh] Waiting... ${i}s"
    sleep 1
done

# Start Streamlit in foreground on Railway's public port
echo "[start.sh] Starting Streamlit on port ${PORT:-8501}..."
exec streamlit run dashboard/frontend/Dashboard.py \
    --server.port "${PORT:-8501}" \
    --server.address "0.0.0.0" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
