FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including wget for healthcheck
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ ./api/
COPY agent/ ./agent/

# Copy static report files (pre-generated)
COPY reports/ ./reports/
COPY logs/ ./logs/
COPY seeds/ ./seeds/

# Create necessary directories
RUN mkdir -p /app/reports/evidence /app/logs /app/demo

EXPOSE 8080

CMD ["python", "-m", "api.main"]
