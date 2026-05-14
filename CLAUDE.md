# Dispatch — agent guidance

## Deployment

This project deploys to **AWS only**, via CDK in `infra/`. There is no Pi,
SSH, systemd, rsync, or Tailscale path. Do not add one.

- Infrastructure: `infra/app.py` (single `MorningDigestStack`)
- API runtime: AWS Lambda via AWS Lambda Web Adapter — `Dockerfile.lambda` runs uvicorn on port 8080; LWA bridges the Lambda Runtime API to plain HTTP. The Function URL is configured for `RESPONSE_STREAM` so SSE flows through CloudFront unbuffered (used by the chat-tab chatbot at `/api/chat/stream`).
- Curation runtime: ECS Fargate (`Dockerfile.fargate`, entry `digest.runner_fargate`),
  scheduled hourly by EventBridge Scheduler
- Storage: S3 data bucket (`MORNING_DIGEST_S3_BUCKET` env var triggers `S3Store`)
- Frontend: PWA built to `pwa/dist/`, hosted on S3, fronted by CloudFront
- Secrets: SSM Parameter Store under `/morning-digest/*`, resolved at cold start
  by `server.config.resolve_ssm_env_vars()`

To deploy: `make deploy` (= `make deploy-infra` then `make deploy-pwa`).

## Verifying production changes

After any deployment (`make deploy` or `make deploy-pwa`), **always** verify before
declaring the task done:

1. **`make test-e2e`** — runs Playwright smoke tests against the live CloudFront URL.
   Tests check: page load, SPA routing, API health, and no missing static assets.
   If any test fails, investigate and fix before finishing.
2. **Visual inspection** — run `make url` to get the live CloudFront URL, then navigate
   to it with the Chrome MCP (`mcp__Claude_in_Chrome__navigate`) and take a screenshot
   to confirm the UI renders correctly.

```
make url          # print the live CloudFront URL
make test-e2e     # run Playwright smoke tests against live deployment
```

## Local development

The filesystem `Store` and `runner.py --data-dir ./data` exist **only for local
development and tests** — integration tests use the filesystem store, and
`make smoke` runs one real agent against `./data`. They are not a deployment
target. Anything you build that's intended for production must work through
the S3 path (set `MORNING_DIGEST_S3_BUCKET` and the app picks `S3Store`
automatically via `server.config.make_store()`).

- `make dev` — uvicorn (8000) + vite (5173), `/api` proxied to uvicorn
- `make smoke` — fast scripted integration test (no creds, ~5s)
- `make smoke-real` — one real Claude agent run against `./data` (needs `CLAUDE_CODE_OAUTH_TOKEN`)
- `make check` — full CI gate (fmt, lint, typecheck, tests)

## Conventions

- Python deps: single source of truth is `pyproject.toml` + `uv.lock`. Both
  `Dockerfile.lambda` and `Dockerfile.fargate` resolve from the lock file via
  `uv export` / `uv sync` — no separate hand-maintained pin files. The API
  Lambda installs the same runtime deps as Fargate (the `/api/chat/stream`
  route imports `digest.agent`, which transitively pulls the curation deps).
- Two storage backends share a single interface: `digest.store.Store` (filesystem)
  and `digest.s3_store.S3Store`. New code that touches storage must go through
  this interface, not directly with `pathlib` or `boto3`.
- Two runner entry points: `digest.runner` (CLI, accepts `--data-dir` or
  `--bucket`) and `digest.runner_fargate` (container entry, S3 only). Keep
  `runner_fargate` thin — it resolves SSM and calls `run_once`.
