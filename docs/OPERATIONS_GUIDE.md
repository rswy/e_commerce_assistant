# Operations Guide
## Customer Service AI — Run, Troubleshoot, Deploy

---

## Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| Docker Desktop | 24+ | Enable at least 8 GB RAM in Docker settings |
| Docker Compose | v2 (built-in) | Use `docker compose`, not `docker-compose` |
| Python | 3.11+ | Only needed for local dev / tests |
| Disk space | 4 GB free | Ollama models: smollm2:135m ~271 MB, llama3.2:3b ~2 GB |
| RAM (container) | 4 GB | 8 GB recommended for llama3.2:3b |

---

## Part 1 — Local Start, Shutdown, Restart

### First-time setup

```bash
cd /Users/ranonsim/Downloads/aiengineer-llm-python-prod

# 1. Create your .env file from the template
cp .env.example .env
# Edit .env if needed (defaults are fine for local dev)

# 2. Run the automated setup script (pulls models, installs Python deps)
bash setup.sh
```

The setup script takes 3–7 minutes on first run — it pulls the Ollama models.

---

### Start: development mode (fastest iteration)

This mode runs Ollama + Phoenix + Redis locally in Docker, but runs the FastAPI app directly on your machine with hot reload. No nginx layer.

```bash
# Terminal 1 — start infrastructure only (no --profile full = no app/nginx/prometheus)
docker compose up -d ollama phoenix redis

# Wait for ollama to be healthy
docker compose ps   # check STATUS column

# Terminal 2 — run the app directly with hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

App is available at `http://localhost:8000`.

---

### Start: full stack mode (production-equivalent)

Runs everything in containers: app + nginx + prometheus + ollama + phoenix + redis.

```bash
docker compose --profile full up -d
```

Wait for all services to become healthy (about 90 seconds on first run after models are cached):

```bash
docker compose ps
# All services should show "healthy" or "running"
```

Verify:
```bash
curl http://localhost:8000/health
# {"status": "ok", "ollama": "up", ...}
```

Service ports when running full stack:

| Service | URL |
|---------|-----|
| App (via nginx) | http://localhost:80 |
| App (direct) | http://localhost:8000 |
| Phoenix UI | http://localhost:6006 |
| Prometheus | http://localhost:9090 |
| Ollama API | http://localhost:11434 |
| Redis | localhost:6379 |

---

### Start: infrastructure only (for running tests)

```bash
# Start just what the test suite needs
docker compose up -d ollama phoenix redis

# Run tests (no app container needed — tests use FastAPI TestClient)
pytest tests/test_app.py -v
```

---

### Shutdown

```bash
# Stop all containers, keep volumes (model cache preserved)
docker compose --profile full down

# Stop and DELETE all data (volumes wiped — re-downloads models next start)
docker compose --profile full down -v
```

To stop only specific services:
```bash
docker compose stop app nginx
docker compose start app nginx
```

---

### Restart

```bash
# Restart everything
docker compose --profile full restart

# Restart only the app (e.g. after a config change)
docker compose restart app

# Rebuild and restart the app after code changes
docker compose --profile full up -d --build app
```

---

### Switching models

To use llama3.2:3b instead of smollm2:135m:

```bash
# Pull the model first (one time, ~2 GB)
docker exec ollama ollama pull llama3.2:3b

# Update .env
LLM_MODEL=llama3.2:3b
LLM_TIMEOUT_SECONDS=60   # larger model is slower

# Restart the app to pick up the new env var
docker compose restart app
# Or if running with uvicorn directly, restart the process
```

---

### Running scenarios without Docker (mock demo)

```bash
# Run all 9 scenarios with a mocked LLM — no Docker needed
python demos/mock_demo.py

# Run a specific scenario
python demos/mock_demo.py --scenario F
```

---

## Part 2 — Troubleshooting

### Health check first

```bash
curl http://localhost:8000/health
```

A healthy response:
```json
{"status": "ok", "ollama": "up", "model": "smollm2:135m"}
```

A degraded response means Ollama is unreachable — see Ollama issues below.

---

### Issue: Ollama won't start or stays unhealthy

**Symptoms:** `docker compose ps` shows ollama as `unhealthy`. App returns 503.

**Diagnose:**
```bash
docker logs ollama
docker exec ollama ollama list
```

**Fix 1 — Not enough memory.** Docker Desktop defaults to 2 GB. Go to Docker Desktop → Settings → Resources → increase Memory to at least 6 GB for smollm2, 8 GB for llama3.2:3b.

**Fix 2 — Port 11434 already in use.** If you have Ollama installed natively and running:
```bash
# Check what's using the port
lsof -i :11434
# Stop native Ollama
ollama stop   # or: pkill ollama
# Then restart
docker compose up -d ollama
```

**Fix 3 — Container in a bad state.** Force-recreate it:
```bash
docker compose rm -f ollama
docker compose up -d ollama
```

---

### Issue: Model not found (404 from Ollama)

**Symptoms:** App returns 500. Logs show `model "smollm2:135m" not found`.

**Fix:**
```bash
# Check what's pulled
docker exec ollama ollama list

# Pull the missing model
docker exec ollama ollama pull smollm2:135m
docker exec ollama ollama pull nomic-embed-text

# Or re-run the init container
docker compose up ollama-init
```

---

### Issue: App returns 504 Gateway Timeout

**Symptoms:** POST /query returns HTTP 504 with `{"detail": "LLM request timed out"}`.

**Cause:** The LLM took longer than `LLM_TIMEOUT_SECONDS` (default 30s).

**Fix:**
```bash
# In .env, increase the timeout
LLM_TIMEOUT_SECONDS=60

# Restart app
docker compose restart app
# Or Ctrl+C and rerun uvicorn
```

For llama3.2:3b on CPU, set `LLM_TIMEOUT_SECONDS=120`.

---

### Issue: Port conflict (address already in use)

**Symptoms:** `docker compose up` fails with `Bind for 0.0.0.0:8000 failed: port is already allocated`.

**Fix:**
```bash
# Find what's on the port
lsof -i :8000

# Kill it, or change the port mapping in docker-compose.yaml:
ports:
  - "8001:8000"   # external:internal

# Then access app at localhost:8001
```

---

### Issue: Rate limiter blocking your own requests

**Symptoms:** You get HTTP 429 after a few rapid requests. Common when running manual tests.

**Cause:** The in-memory rate limiter tracks per-IP (20 req/min default).

**Fix for local testing:** Use different IPs by adding the `X-Forwarded-For` header, or just wait a minute. To raise the limit locally:
```bash
# In .env
RATE_LIMIT_PER_MINUTE=1000
# Restart app
```

---

### Issue: Authentication returning 403

**Symptoms:** All requests return `{"detail": "Missing or invalid API key"}`.

**Cause:** `API_KEY` is set in your env but you're not sending it in requests.

**Fix:** Either clear `API_KEY` in `.env` (disables auth, fine for local dev), or include the header:
```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Where is my order ORD-10001?"}'
```

---

### Issue: Phoenix traces not appearing

**Symptoms:** Phoenix UI at localhost:6006 shows no traces.

**Fix 1 — Check env vars:**
```bash
# In .env
PHOENIX_ENABLED=true
PHOENIX_ENDPOINT=http://localhost:6006   # for local dev
# In docker-compose, the app uses http://phoenix:6006 (service name)
```

**Fix 2 — Phoenix container not running:**
```bash
docker compose up -d phoenix
docker logs phoenix
```

**Fix 3 — Wrong endpoint for Docker.** When the app runs in a container, it must use the service name, not localhost:
```bash
PHOENIX_ENDPOINT=http://phoenix:6006   # correct inside Docker network
PHOENIX_ENDPOINT=http://localhost:6006  # correct when running uvicorn directly
```

---

### Issue: Tests failing with rate limiter errors

**Symptoms:** `test_rate_limiting` fails or other tests intermittently get 429.

**Fix:** The test suite calls `limiter._storage.reset()` before rate-limit-sensitive tests. If you see this on a clean run, check that the `client_with_mock_llm` fixture is being used (not the plain `client` fixture) for tests that send many requests.

---

### Reading the logs

```bash
# App logs
docker logs customer-service-app -f

# Ollama logs
docker logs ollama -f

# All services
docker compose logs -f

# Filter for errors only
docker compose logs | grep -i error
```

In development (`APP_ENV=development`), logs are colored and human-readable. In production (`APP_ENV=production`), logs are structured JSON — pipe to `jq` for readability:
```bash
docker logs customer-service-app | jq '.'
```

---

## Part 3 — Cloud Deployment

### Overview

The production deployment path:

```
Local build → Push to registry → Pull on server → docker compose (prod overrides)
```

The `docker-compose.prod.yaml` file contains all production overrides: image reference, resource limits, restart policies, no direct port exposure for app (traffic goes through nginx).

---

### Step 1 — Build and push the Docker image

```bash
export REGISTRY=ghcr.io/your-org    # or your registry
export IMAGE_TAG=$(git rev-parse --short HEAD)

# Build
docker build -t ${REGISTRY}/customer-service-ai:${IMAGE_TAG} .
docker tag ${REGISTRY}/customer-service-ai:${IMAGE_TAG} \
           ${REGISTRY}/customer-service-ai:latest

# Push
docker login ghcr.io   # or: aws ecr get-login-password | docker login ...
docker push ${REGISTRY}/customer-service-ai:${IMAGE_TAG}
docker push ${REGISTRY}/customer-service-ai:latest
```

CI does this automatically on every push to `main` via `.github/workflows/ci.yml`.

---

### Step 2 — Provision the server

**Minimum spec for smollm2:135m:** 2 vCPU, 4 GB RAM, 20 GB disk  
**Recommended for llama3.2:3b:** 4 vCPU, 8 GB RAM, 40 GB disk  
**For GPU inference (llama3.2:3b):** NVIDIA T4 or better (AWS g4dn.xlarge, GCP n1-standard-4+T4)

Tested cloud options:

| Provider | Instance type | Notes |
|----------|--------------|-------|
| AWS ECS (Fargate) | 2 vCPU / 4 GB | No GPU; smollm2 only |
| AWS EC2 | g4dn.xlarge | GPU; llama3.2:3b viable |
| DigitalOcean Droplet | 4 GB / 2 vCPU | Simple; no GPU |
| Fly.io | shared-cpu-4x | Easy deploys; limited RAM |
| Railway | Standard plan | Good for demos; no GPU |
| Hetzner CPX31 | 8 GB / 4 vCPU | Best price/performance for CPU |

---

### Step 3 — Configure the server

```bash
# SSH into the server, then:

# Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker $USER

# Clone the repo (for compose files and nginx config only — no code needed)
git clone https://github.com/your-org/your-repo.git
cd your-repo

# Create production .env
cp .env.example .env
```

Edit `.env` for production:
```bash
API_KEY=$(openssl rand -hex 32)    # generate a strong key
APP_ENV=production
LOG_LEVEL=INFO
LLM_MODEL=smollm2:135m            # or llama3.2:3b if RAM allows
OLLAMA_BASE_URL=http://ollama:11434
LLM_TIMEOUT_SECONDS=30
RATE_LIMIT_PER_MINUTE=20
PHOENIX_ENABLED=true
PHOENIX_ENDPOINT=http://phoenix:6006
METRICS_ENABLED=true
REGISTRY=ghcr.io/your-org
IMAGE_TAG=latest
```

---

### Step 4 — Start the production stack

```bash
# Pull the pre-built image (instead of building on the server)
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml \
  --profile full pull

# Start everything
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml \
  --profile full up -d

# Check status
docker compose ps
curl http://localhost/health    # through nginx on port 80
```

Key differences from `docker-compose.prod.yaml`:
- App uses the registry image (`ghcr.io/your-org/customer-service-ai:latest`) — no local build
- App port 8000 is NOT exposed externally — all traffic goes through nginx on 80/443
- Prometheus port 9090 is NOT exposed — access via SSH tunnel only
- `restart: always` (vs `unless-stopped` in dev)
- CPU and memory limits applied

---

### Step 5 — TLS with Let's Encrypt

```bash
# On the server
apt install certbot
certbot certonly --standalone -d yourdomain.com

# Certificates land in /etc/letsencrypt/live/yourdomain.com/
# docker-compose.prod.yaml already mounts /etc/letsencrypt into nginx
```

Then uncomment the HTTPS server block in `nginx/nginx.conf` and the `443:443` port mapping in `docker-compose.prod.yaml`.

---

### Step 6 — Access Prometheus and Phoenix securely

In production, Prometheus and Phoenix are not exposed on public ports (`ports: !reset []` in docker-compose.prod.yaml). Access them via SSH tunnel:

```bash
# Prometheus
ssh -L 9090:localhost:9090 user@your-server

# Phoenix
ssh -L 6006:localhost:6006 user@your-server

# Then open in your browser:
# http://localhost:9090  (Prometheus)
# http://localhost:6006  (Phoenix)
```

---

### Deploying updates

```bash
# On your local machine — build and push
export IMAGE_TAG=$(git rev-parse --short HEAD)
docker build -t ${REGISTRY}/customer-service-ai:${IMAGE_TAG} .
docker push ${REGISTRY}/customer-service-ai:${IMAGE_TAG}
docker push ${REGISTRY}/customer-service-ai:latest

# On the server — pull and restart app only (zero-downtime with health check)
export IMAGE_TAG=latest
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml pull app
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml \
  --profile full up -d --no-deps app
```

Only the `app` container restarts — Ollama, Redis, Phoenix keep running.

---

### Scaling horizontally (future state)

The app is designed for horizontal scaling but currently uses in-memory session state and rate limiting. Two changes enable true horizontal scaling:

1. **Switch session store to Redis** — the `InMemorySessionStore` in `app/agents/session_store.py` has a Redis-compatible interface; swap the implementation.
2. **Switch slowapi to Redis backend** — `limiter = Limiter(key_func=get_remote_address, storage_uri="redis://redis:6379")`.

After those changes, run multiple app replicas behind nginx:
```yaml
# docker-compose.prod.yaml
app:
  deploy:
    replicas: 3
```

The bottleneck then becomes Ollama. For GPU-backed inference at scale, replace Ollama with a hosted inference endpoint (Groq, Replicate, or self-hosted vLLM) by updating `OLLAMA_BASE_URL` to point at the external endpoint.

---

## Quick Reference

```bash
# First time
bash setup.sh

# Dev start (hot reload)
docker compose up -d ollama phoenix redis
uvicorn app.main:app --reload

# Full stack start
docker compose --profile full up -d

# Stop (keep data)
docker compose --profile full down

# Stop (wipe data)
docker compose --profile full down -v

# Rebuild app after code changes
docker compose --profile full up -d --build app

# Switch model
docker exec ollama ollama pull llama3.2:3b
# edit .env → LLM_MODEL=llama3.2:3b
docker compose restart app

# Run tests
pytest tests/test_app.py -v

# Run evaluation
python evaluation/evaluate.py

# Mock demo (no Docker)
python demos/mock_demo.py

# View logs
docker compose logs -f app
docker compose logs -f ollama

# Check health
curl http://localhost:8000/health

# Review queue (escalated conversations)
curl http://localhost:8000/admin/review-queue

# Prometheus (full stack)
open http://localhost:9090

# Phoenix traces (full stack)
open http://localhost:6006
```
