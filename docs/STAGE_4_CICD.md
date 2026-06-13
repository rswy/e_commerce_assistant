# Stage 4 — CI/CD Pipeline

## Pipeline Stages

```
┌──────────┐     ┌──────────┐
│   lint   │     │   test   │
│          │     │          │
│ ruff     │     │ pytest   │
│ mypy     │     │ coverage │
│          │     │ codecov  │
└────┬─────┘     └────┬─────┘
     │                │
     │          ┌─────▼──────┐
     │          │  evaluate  │
     │          │            │
     │          │ start app  │
     │          │ (mocked)   │
     │          │ run eval   │
     │          │ quality    │
     │          │ gate ≥0.95 │
     │          └─────┬──────┘
     │                │
     └────────┬───────┘
              │
        ┌─────▼──────────┐
        │  docker-build  │
        │                │
        │ buildx + cache │
        └─────┬──────────┘
              │
         (main branch only)
              │
        ┌─────▼──────────┐
        │ deploy-staging │
        │                │
        │ push image     │
        │ deploy         │
        │ smoke test     │
        └────────────────┘
```

## Quality Gates

| Gate | Condition | Failure Action |
|------|-----------|----------------|
| Lint | `ruff check` returns 0 | Pipeline fails, block merge |
| Type check | `mypy` returns 0 | Warning only (`continue-on-error: true`) |
| Unit tests | All tests pass | Pipeline fails, block merge |
| Coverage | `--cov-fail-under=80` | Pipeline fails, block merge |
| Evaluation | `blocking_accuracy >= 0.95` AND `avg_similarity >= 0.30` | Pipeline fails, block merge |
| Docker build | Image builds successfully | Pipeline fails, block merge |

## How to Run Locally

```bash
# 1. Install dev dependencies
pip install -e ".[dev]"
pip install pytest-cov

# 2. Lint
ruff check app/ tests/ evaluation/
ruff format --check app/ tests/ evaluation/

# 3. Type check
mypy app/ --ignore-missing-imports

# 4. Tests with coverage
PHOENIX_ENABLED=false pytest tests/ --ignore=tests/integration \
  -v --cov=app --cov-report=term-missing --cov-fail-under=80

# 5. Integration tests (requires live Ollama + running app)
INTEGRATION_TESTS=true pytest tests/integration/ -v

# 6. Evaluation quality gate (requires running app)
python evaluation/evaluate.py \
  --base-url http://localhost:8000 \
  --sample-size 20 \
  --quality-gate
```

## Environment Variables for CI

These should be set as GitHub Actions secrets or repository variables:

| Variable | Where | Purpose |
|----------|-------|---------|
| `CODECOV_TOKEN` | Secret | Upload coverage reports to Codecov |
| `REGISTRY_URL` | Secret | Container registry URL for image push |
| `REGISTRY_USER` | Secret | Registry login username |
| `REGISTRY_PASSWORD` | Secret | Registry login password |
| `STAGING_URL` | Secret | Staging environment URL for smoke test |

These can be set as repository-level variables (non-sensitive):

| Variable | Example Value | Purpose |
|----------|---------------|---------|
| `PYTHON_VERSION` | `3.11` | Python version for all jobs |

## Deploy Process

The `deploy-staging` job runs only on pushes to the `main` branch and only
after both `docker-build` and `evaluate` pass.

The placeholder steps in `ci.yml` show the structure — replace the `echo`
commands with your actual deployment mechanism:

- **Kubernetes**: `kubectl set image deployment/customer-service ...`
- **AWS ECS**: `aws ecs update-service --cluster ... --service ...`
- **Fly.io**: `flyctl deploy --image ...`
- **Docker Swarm**: `docker service update --image ...`

The `environment: staging` declaration in the job unlocks GitHub's
environment protection rules (required reviewers, deployment branch policy,
etc.).

## Branch Protection Recommendations

Configure the `main` branch with:
- Require all 4 status checks to pass: `lint`, `test`, `evaluate`, `docker-build`
- Require at least 1 approving review
- Dismiss stale reviews on new pushes
- Require linear history
