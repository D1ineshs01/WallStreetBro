#!/bin/bash
# Wall Street Bro — Railway startup script
# FastAPI on $PORT (Railway's public port — passes health checks)
# Streamlit on 8501 (internal only — Streamlit calls FastAPI via localhost:$PORT)

set -e

export FASTAPI_PORT=${PORT:-8000}
export STREAMLIT_SERVER_PORT=8501
export STREAMLIT_SERVER_ADDRESS=0.0.0.0
export STREAMLIT_SERVER_HEADLESS=true
export STREAMLIT_SERVER_ENABLE_CORS=false
export STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

# API_BASE_URL tells Streamlit where FastAPI lives (same container, Railway's port)
export API_BASE_URL="http://localhost:${FASTAPI_PORT}"

echo "Starting FastAPI on port $FASTAPI_PORT..."
python main.py --mode all &
FASTAPI_PID=$!

# Wait for FastAPI to be ready before starting Streamlit
echo "Waiting for FastAPI to be ready..."
for i in $(seq 1 20); do
    if curl -sf "http://localhost:${FASTAPI_PORT}/health" > /dev/null 2>&1; then
        echo "FastAPI is ready."
        break
    fi
    sleep 1
done

echo "Starting Streamlit on port $STREAMLIT_SERVER_PORT..."
streamlit run dashboard/frontend/Dashboard.py &
STREAMLIT_PID=$!

wait -n $FASTAPI_PID $STREAMLIT_PID
