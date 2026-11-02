FROM python:3.11-slim

# Install system dependencies (FFmpeg for merging, Node.js for yt-dlp JS challenges)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application files
COPY . .

# Environment variables
ENV PORT=5050
ENV PYTHONUNBUFFERED=1

EXPOSE 5050

# Run with Gunicorn, dynamically reading $PORT (defaults to 5050)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5050} --workers 2 --threads 4 --timeout 300 app:app"]
