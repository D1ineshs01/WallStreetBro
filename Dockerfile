FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Make startup script executable
RUN chmod +x start.sh

# Railway exposes $PORT externally — Streamlit binds to it.
# FastAPI runs internally on 8000.
EXPOSE 8000

# API_BASE_URL tells Streamlit where to find FastAPI (localhost since same container)
ENV API_BASE_URL=http://localhost:8000

CMD ["bash", "start.sh"]
