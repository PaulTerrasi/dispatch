# Dispatch

A personal iPhone PWA that delivers a Claude-curated news feed — articles
and YouTube videos *you specifically* would find valuable, refreshed hourly.
Backend runs on AWS; frontend is a PWA installed to the iPhone home screen.

See [the design doc](docs/plan.md) for the full architecture.

## Architecture

```
                          CloudFront
                         /          \
              S3 (PWA static)        Lambda Function URL (response streaming)
                                     - FastAPI behind AWS Lambda Web Adapter
                                     - serves /api/* incl. SSE /api/chat/stream
                                     - spawns reflection tasks on feedback

       EventBridge Scheduler  ──hourly──▶  ECS Fargate task
                                            (digest.runner_fargate)
                                                  │
                                                  ▼
                                       S3 data bucket  ◀── Lambda reads here too

       SSM Parameter Store: /morning-digest/claude-oauth-token,
                            /morning-digest/auth-token
                            (resolved at Lambda/Fargate cold start)
```

All infrastructure is defined in [`infra/app.py`](infra/app.py) as a single
CDK stack (`MorningDigestStack`).

## Quick reference

| Make target        | What it does                                                |
| ------------------ | ----------------------------------------------------------- |
| `make install`     | `uv sync` + `npm install`                                   |
| `make fmt`         | ruff format + prettier                                      |
| `make lint`        | ruff check + eslint                                         |
| `make typecheck`   | mypy --strict + tsc --noEmit                                |
| `make test`        | pytest (incl. integration) + vitest                         |
| `make check`       | the full CI gate                                            |
| `make types`       | regenerate `pwa/src/api-types.ts` from FastAPI's schema     |
| `make dev`         | uvicorn + vite, with `/api` proxied to uvicorn              |
| `make build`       | vite build → `server/static/` and `pwa/dist/`               |
| `make smoke`       | one *real* agent run against the live SDK (needs token)     |
| `make deploy-infra`| `cdk deploy` the AWS stack                                  |
| `make deploy-pwa`  | build the PWA and sync to S3 + invalidate CloudFront        |
| `make deploy`      | `deploy-infra` then `deploy-pwa`                            |

## Local development

The local Python store is a filesystem `Store` (`./data/`). It exists for
tests and `make smoke` only — it is **not a deployment target**.

```bash
make install
cp .env.example .env   # paste your CLAUDE_CODE_OAUTH_TOKEN
make dev               # uvicorn (8000) + vite (5173); /api proxied
```

Run the curation loop locally without Claude (stubbed agent):

```bash
uv run pytest tests/integration/test_runner.py -v
```

Run it *with* Claude (real subscription auth):

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...   # from `claude setup-token`
make smoke
```

## Deploying to AWS

### One-time setup

```bash
# 1. CDK bootstrap your account/region (once per account)
cd infra && cdk bootstrap

# 2. Seed SSM Parameter Store with the secrets the app reads at cold start
aws ssm put-parameter --name /morning-digest/claude-oauth-token \
  --type SecureString --value "$(claude setup-token)"

aws ssm put-parameter --name /morning-digest/auth-token \
  --type SecureString --value "$(openssl rand -hex 32)"
```

### Deploy

```bash
make deploy
```

This runs `cdk deploy` (creates/updates Lambda, Fargate task def, S3 buckets,
CloudFront distribution, EventBridge schedule), then builds the PWA, syncs it
to the frontend bucket, and invalidates CloudFront.

The CDK stack outputs the CloudFront URL — open it in mobile Safari and use
**Share → Add to Home Screen** to install the PWA.

### Token rotation

`CLAUDE_CODE_OAUTH_TOKEN` is valid for one year. To rotate:

```bash
aws ssm put-parameter --name /morning-digest/claude-oauth-token \
  --type SecureString --value "$(claude setup-token)" --overwrite
```

Lambda and Fargate read SSM at cold start, so new invocations pick up the
new value automatically. To force an immediate rotation, redeploy the Lambda
(`make deploy-infra`) — that recycles all warm containers.

## Layout

```
server/                  FastAPI app + routes (Lambda + local)
digest/                  agent + tools + runners + prompts
  runner.py              CLI entry point: --data-dir (local) | --bucket (AWS)
  runner_fargate.py      ECS Fargate container entry
  store.py               filesystem Store (local dev / tests)
  s3_store.py            S3Store (production)
infra/                   AWS CDK app (single MorningDigestStack)
pwa/                     vanilla TS + Vite PWA
tests/                   unit + integration
data/                    git-tracked content store (local dev only)
Dockerfile.fargate       container image for the Fargate runner
Dockerfile.lambda        container image for the API Lambda
```

`data/` is local-only; in production all state lives in the S3 data bucket
(versioned, with old versions transitioning to Glacier after 90 days).
`git log -p data/profile.md` is the audit trail of agent edits to your
profile when running locally; the same audit lives in S3 object versions
in production.

## Out of scope (for now)

- Multi-user / auth beyond a shared bearer token
- Push notifications
- Read-later / save-for-offline
- Podcasts
- Native iOS app

## Icons

The manifest references `/icons/icon-192.png` and `/icons/icon-512.png` which
are not yet generated. Drop two PNGs into `pwa/public/icons/` (or generate
from an SVG) before "Add to Home Screen" for a proper icon.
