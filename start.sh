#!/bin/bash
# Wall Street Bro — Railway startup script
#
# Architecture:
#   FastAPI  → port 8000 (internal only, starts first)
#   Streamlit → $PORT   (Railway's public port, starts after FastAPI is ready)
#
# Streamlit calls FastAPI via localhost:8000
# Users access the Streamlit dashboard via the public Railway URL

set -e

export FASTAPI_PORT=8000
export API_BASE_URL="http://localhost:8000"

echo "[start.sh] Starting FastAPI on port $FASTAPI_PORT..."
python main.py --mode all &
FASTAPI_PID=$!

# Poll until FastAPI is ready (up to 30s) before starting Streamlit
echo "[start.sh] Waiting for FastAPI to be ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${FASTAPI_PORT}/health" > /dev/null 2>&1; then
        echo "[start.sh] FastAPI ready after ${i}s."
        break
    fi
    sleep 1
done

echo "[start.sh] Starting Streamlit on port ${PORT:-8501}..."
streamlit run dashboard/frontend/Dashboard.py \
    --server.port "${PORT:-8501}" \
    --server.address "0.0.0.0" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false &
STREAMLIT_PID=$!

# Exit if either process dies
wait -n $FASTAPI_PID $STREAMLIT_PID
