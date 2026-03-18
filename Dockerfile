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

# Railway exposes $PORT externally — FastAPI binds to it (passes health checks).
# Streamlit runs internally on 8501 and calls FastAPI via localhost:$PORT.
EXPOSE 8000

CMD ["bash", "start.sh"]
