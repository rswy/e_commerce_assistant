"""Configuration settings for the customer service application.

All settings are read from environment variables with safe defaults.
Set APP_ENV=production in production deployments to enable stricter behavior.
"""

import os

# ---------------------------------------------------------------------------
# Ollama / LLM settings
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "smollm2:135m")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

# ---------------------------------------------------------------------------
# Application server settings
# ---------------------------------------------------------------------------
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

# ---------------------------------------------------------------------------
# Security settings
# ---------------------------------------------------------------------------
# Leave empty to disable API key authentication (development mode).
# In production, set to a strong random string (e.g. openssl rand -hex 32).
API_KEY = os.getenv("API_KEY", "")

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Maximum number of requests per minute per IP address.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

# ---------------------------------------------------------------------------
# LLM timeout
# ---------------------------------------------------------------------------
# Seconds to wait for an LLM response before returning a 504.
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Python log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# "development" or "production".  Controls log format and other behaviors.
APP_ENV = os.getenv("APP_ENV", "development")

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
# Set to "false" to disable Prometheus metrics endpoint.
METRICS_ENABLED = os.getenv("METRICS_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Redis (session state + future rate-limit store)
# ---------------------------------------------------------------------------
# Empty string → use in-memory fallback (single-instance dev mode).
REDIS_URL = os.getenv("REDIS_URL", "")

# ---------------------------------------------------------------------------
# Multi-agent system
# ---------------------------------------------------------------------------
REVIEW_QUEUE_ENABLED = os.getenv("REVIEW_QUEUE_ENABLED", "true").lower() == "true"
SESSION_MAX_TURNS = int(os.getenv("SESSION_MAX_TURNS", "10"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))

# ---------------------------------------------------------------------------
# Phoenix observability settings
# ---------------------------------------------------------------------------
PHOENIX_ENDPOINT = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006")
PHOENIX_ENABLED = os.getenv("PHOENIX_ENABLED", "false").lower() == "true"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a helpful customer service assistant for an e-commerce retail company.
You assist customers with:
- Order status and tracking
- Shipping and delivery questions
- Returns and refunds
- Product information
- Account management

Provide clear, concise, and helpful responses. If you cannot help with a request,
politely explain why."""
