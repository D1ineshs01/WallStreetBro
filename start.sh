#!/bin/bash
# Wall Street Bro — Railway startup script
# Runs FastAPI on port 8000 (internal) and Streamlit on $PORT (Railway's external port)
# Streamlit is what Railway exposes publicly. FastAPI is internal-only.

set -e

# FastAPI always on 8000 internally (Streamlit calls localhost:8000)
export FASTAPI_PORT=8000

# Streamlit listens on Railway's assigned PORT
export STREAMLIT_PORT=${PORT:-8501}

echo "Starting FastAPI on port $FASTAPI_PORT..."
python main.py --mode all &
FASTAPI_PID=$!

# Give FastAPI 5 seconds to start before Streamlit tries to call it
sleep 5

echo "Starting Streamlit on port $STREAMLIT_PORT..."
streamlit run dashboard/frontend/Dashboard.py \
  --server.port "$STREAMLIT_PORT" \
  --server.address "0.0.0.0" \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false &
STREAMLIT_PID=$!

# Wait for either process to exit
wait -n $FASTAPI_PID $STREAMLIT_PID
