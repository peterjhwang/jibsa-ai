FROM python:3.12-slim

# Install Node.js (required for Claude CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

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

# Volumes for credentials and runtime data are defined in docker-compose.yml
ENTRYPOINT ["python", "-m", "src.app"]
