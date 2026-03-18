#!/bin/bash
# Wall Street Bro — Railway startup
# FastAPI:   port 8000  (internal, dual-stack ::  so localhost works on IPv4 + IPv6)
# Streamlit: $PORT      (Railway's public port — what users see)

export FASTAPI_PORT=8000
export API_BASE_URL="http://localhost:8000"

# Start FastAPI in background
echo "[start] FastAPI starting on port 8000..."
python main.py --mode all &
FASTAPI_PID=$!

# Wait for FastAPI to be ready before starting Streamlit
echo "[start] Waiting for FastAPI..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "[start] FastAPI ready after ${i}s"
        break
    fi
    sleep 1
done

# Start Streamlit in foreground — keeps container alive
echo "[start] Streamlit starting on port ${PORT:-8501}..."
exec streamlit run dashboard/frontend/Dashboard.py \
    --server.port "${PORT:-8501}" \
    --server.address "0.0.0.0" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
