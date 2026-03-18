FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

# Streamlit on $PORT (public), FastAPI on 8000 (internal via 127.0.0.1)
EXPOSE 8000
EXPOSE 8501

# Inline startup — bypasses any Railway custom start command caching issues.
# FastAPI starts first; wait loop ensures it's ready before Streamlit boots.
CMD ["bash", "-c", "\
  export FASTAPI_PORT=8000 && \
  export API_BASE_URL=http://127.0.0.1:8000 && \
  python main.py --mode all & \
  echo 'Waiting for FastAPI...' && \
  for i in $(seq 1 30); do curl -sf http://127.0.0.1:8000/health && echo FastAPI ready && break || sleep 1; done && \
  exec streamlit run dashboard/frontend/Dashboard.py \
    --server.port ${PORT:-8501} \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false \
"]
