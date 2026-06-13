# ============================================================================
# Stage 1 — Builder
# Installs all dependencies into a virtual environment using uv.
# The venv is then copied into the final image, keeping the final layer clean.
# ============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./

# Install only production dependencies (no dev extras) into an explicit venv
# so we can copy just the venv to the final stage.
RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python \
        --no-cache -r pyproject.toml

# ============================================================================
# Stage 2 — Production runtime
# Lean image: no pip, no uv, no test code, no build artifacts.
# ============================================================================
FROM python:3.11-slim AS production

# Create a non-root user to run the application.
RUN groupadd --system appuser && useradd --system --gid appuser appuser

WORKDIR /app

# Copy the pre-built virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy only the application source and data.
# Tests and evaluation scripts are intentionally excluded from the image.
COPY app/     ./app/
COPY data/    ./data/

# Make the venv the default Python environment.
ENV PATH="/opt/venv/bin:$PATH"

# Runtime environment defaults — override at container launch as needed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OLLAMA_BASE_URL=http://ollama:11434 \
    APP_ENV=production \
    LOG_LEVEL=INFO \
    METRICS_ENABLED=true

EXPOSE 8000

# Drop to non-root.
USER appuser

# Use exec form so PID 1 is uvicorn (receives SIGTERM correctly).
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "warning"]
