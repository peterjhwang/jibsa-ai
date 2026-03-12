FROM python:3.12-slim

# Create non-root user
RUN useradd -m -u 1000 jibsa
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY config/ ./config/

# Switch to non-root user
USER jibsa

ENTRYPOINT ["python", "-m", "src.app"]
