#!/bin/bash
# Wall Street Bro — Railway startup
# FastAPI: port 8000 (internal, background) — isolated so it survives agent crashes
# Agent loop: background — can crash/restart without killing the dashboard
# Streamlit: $PORT (Railway's public port, foreground)

export FASTAPI_PORT=8000
export API_BASE_URL="http://localhost:8000"

# Start FastAPI independently — dashboard stays up even if agent has no credits
python main.py --mode api &

# Start agent loop separately — crashes here won't kill FastAPI or Streamlit
python main.py --mode agent &

# Start Streamlit in foreground on Railway's public port
# Running in foreground keeps the container alive and lets Railway
# detect the port as soon as Streamlit boots (~10 seconds)
exec streamlit run dashboard/frontend/Dashboard.py \
    --server.port "${PORT:-8501}" \
    --server.address "0.0.0.0" \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
