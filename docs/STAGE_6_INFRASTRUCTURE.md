# Stage 6 — Production Infrastructure

## Architecture Diagram

```
                         Internet
                            │
                     ┌──────▼──────┐
                     │    nginx    │  :80 / :443
                     │             │
                     │ rate limit  │  20 req/min per IP
                     │ TLS term.   │  (cert via Let's Encrypt)
                     │ sec headers │
                     │ /metrics    │  blocked from external
                     └──────┬──────┘
                            │ proxy_pass (HTTP/1.1, keepalive)
                     ┌──────▼──────────────┐
                     │   FastAPI (app)     │  :8000
                     │                     │
                     │  auth dependency    │  X-API-Key
                     │  slowapi limiter    │  20 req/min per IP
                     │  guardrail pipeline │  5 steps
                     │  asyncio LLM call  │  timeout: 30s
                     │  structlog JSON     │
                     │  prometheus_client  │→ /metrics
                     └──────┬──────────────┘
                            │ HTTP (langchain_ollama)
                     ┌──────▼──────┐
                     │   Ollama    │  :11434
                     │ smollm2:135m│
                     └─────────────┘

         ┌──────────────────┐      ┌────────────────┐
         │   Prometheus     │ ◄────│  /metrics      │
         │ :9090            │      │  (app:8000)    │
         └──────────────────┘      └────────────────┘

         ┌──────────────────┐
         │    Phoenix       │  :6006
         │  (OTLP traces)   │
         └──────────────────┘
```

## nginx Configuration Explained

The nginx configuration at `nginx/nginx.conf` provides:

### Rate Limiting
```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=20r/m;
```
10 MB shared memory zone keyed by binary IP — stores ~80,000 IP counters.
The `burst=10 nodelay` on the location allows short bursts without queuing delay.

### Security Headers
| Header | Value | Purpose |
|--------|-------|---------|
| `X-Frame-Options` | `SAMEORIGIN` | Prevent clickjacking |
| `X-Content-Type-Options` | `nosniff` | Prevent MIME-type sniffing |
| `X-XSS-Protection` | `1; mode=block` | Legacy XSS filter |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Enforce HTTPS for 1 year |
| `Content-Security-Policy` | `default-src 'self'` | Restrict resource origins |
| `Referrer-Policy` | `no-referrer-when-downgrade` | Control referrer header |

### Metrics Endpoint Protection
```nginx
location /metrics {
    allow 172.0.0.0/8;   # Docker bridge network
    allow 127.0.0.1;
    deny  all;            # Block all external access
    proxy_pass http://app_backend;
}
```

### Gzip Compression
JSON responses (the primary payload) are compressed — typically 60–80 %
reduction for payloads > 1 KB.

## TLS Setup Instructions

1. Install `certbot` on the nginx host:
   ```bash
   apt-get install certbot python3-certbot-nginx
   ```

2. Obtain a certificate (ensure port 80 is accessible and DNS is configured):
   ```bash
   certbot certonly --nginx -d yourdomain.com
   ```

3. In `nginx/nginx.conf`:
   - Uncomment the HTTPS server block.
   - Fill in `ssl_certificate` and `ssl_certificate_key` paths.
   - Uncomment the HTTP → HTTPS redirect in the HTTP server block.

4. Auto-renew:
   ```bash
   # Test renewal
   certbot renew --dry-run
   # Add cron job
   echo "0 12 * * * /usr/bin/certbot renew --quiet" | crontab -
   ```

5. If running nginx inside Docker, mount the Let's Encrypt directory:
   ```yaml
   volumes:
     - /etc/letsencrypt:/etc/letsencrypt:ro
   ```

## Environment Separation

| Environment | `APP_ENV` | Auth | Log Format | Profile |
|-------------|-----------|------|------------|---------|
| Development | `development` | Disabled (empty `API_KEY`) | Colored console | default |
| Staging | `production` | Enabled | JSON | `full` |
| Production | `production` | Enabled | JSON | `full` |

Use separate `.env` files per environment and never commit them to git.
Use Docker secrets or a secrets manager (AWS Secrets Manager, HashiCorp Vault)
for the `API_KEY` in staging and production.

## Docker Multi-Stage Build

To reduce the final image size, update `Dockerfile` to use a multi-stage build:

```dockerfile
# Build stage — installs all deps including dev tools
FROM python:3.11-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir build && pip install --no-cache-dir -e .

# Runtime stage — copies only the installed packages
FROM python:3.11-slim AS runtime
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app/ ./app/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

This typically reduces the image from ~1.5 GB to ~250–400 MB.

## Scaling Considerations

### Horizontal Scaling

The app is stateless — all state lives in Ollama (model weights) and the
optional Phoenix service (traces).  You can run multiple app replicas behind
nginx with `upstream` load balancing:

```nginx
upstream app_backend {
    server app1:8000;
    server app2:8000;
    server app3:8000;
    keepalive 64;
}
```

In Docker Swarm or Kubernetes, use the built-in service load balancing instead.

### Ollama Scaling

Ollama is the bottleneck for throughput.  Options in order of complexity:

1. **Increase concurrency** — set `OLLAMA_NUM_PARALLEL` env var on the Ollama
   service to allow multiple simultaneous model runs.
2. **Dedicated GPU node** — move Ollama to a GPU-equipped host; expose via
   `OLLAMA_BASE_URL=http://gpu-host:11434`.
3. **Multiple Ollama instances** — run one per GPU and load-balance at the nginx
   layer with a separate upstream for Ollama.
4. **Upgrade model** — a larger, smarter model (Llama 3.2 3B) on the same GPU
   often produces better quality-per-request, reducing retries from users.

### Rate Limit Tuning

If you have legitimate high-volume clients (e.g. a front-end SPA), increase
`RATE_LIMIT_PER_MINUTE` for trusted IP ranges in nginx:

```nginx
geo $limit {
    default         1;
    10.0.0.0/8      0;   # Internal network — not rate limited
}
map $limit $limit_key {
    0 "";
    1 $binary_remote_addr;
}
limit_req_zone $limit_key zone=api:10m rate=20r/m;
```
