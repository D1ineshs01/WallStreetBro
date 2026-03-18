#!/bin/bash
# Wall Street Bro — Railway startup script
# FastAPI on $PORT (Railway's public port — passes health checks)
# Streamlit on 8501 (internal — calls FastAPI via localhost:$PORT)
# API_BASE_URL is set from Railway Variables (or defaults to localhost:$PORT)

set -e

export FASTAPI_PORT=${PORT:-8000}

# If API_BASE_URL not set in Railway Variables, default to localhost
if [ -z "$API_BASE_URL" ]; then
    export API_BASE_URL="http://localhost:${FASTAPI_PORT}"
fi

echo "Starting FastAPI on port $FASTAPI_PORT..."
echo "Streamlit will call FastAPI at: $API_BASE_URL"
python main.py --mode all &
FASTAPI_PID=$!

# Wait for FastAPI to be ready before starting Streamlit
echo "Waiting for FastAPI to be ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${FASTAPI_PORT}/health" > /dev/null 2>&1; then
        echo "FastAPI ready after ${i}s"
        break
    fi
    sleep 1
done

echo "Starting Streamlit on port 8501..."
streamlit run dashboard/frontend/Dashboard.py \
    --server.port 8501 \
    --server.address "0.0.0.0" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false &
STREAMLIT_PID=$!

wait -n $FASTAPI_PID $STREAMLIT_PID
