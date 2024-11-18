# Use official Python image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    curl \  
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create logs directory
RUN mkdir -p /app/logs && \
    touch /app/logs/app.log

# Create a non-root user and switch to it
RUN useradd -m appuser && \
    chown -R appuser:appuser /app
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DOTENV_PATH=/app/.env

# Add a healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Run the application with logging
CMD ["sh", "-c", "python -u diogrid.py 2>&1 | tee -a /app/logs/app.log"]