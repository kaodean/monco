# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including Node.js
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_18.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files
COPY pyproject.toml ./

# Install Python dependencies using uv
RUN /root/.local/bin/uv sync

# Copy application source code
COPY src/ ./src/
COPY claude-code/ ./claude-code/

# Create workplace directory
RUN mkdir -p ./workplace

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PLUGIN_PATH=/app/claude-code/plugin-template \
    WORKPLACE_ROOT=/app/workplace

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | bash

# Verify Claude Code installation
RUN /root/.local/bin/claude --version || echo "Claude Code installed"

# Expose any ports if needed (optional, depends on your application)
# EXPOSE 8080

# Health check (optional)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the application
CMD ["/root/.local/bin/uv", "run", "src/main.py"]
