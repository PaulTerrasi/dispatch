# Project lives under ~/Documents, which is iCloud-synced. The `.nosync`
# suffix opts the venv out of sync; without this, iCloud creates conflict
# copies inside .venv/ and uv warns about missing RECORD files.
export UV_PROJECT_ENVIRONMENT := .venv.nosync

.PHONY: install fmt fmt-check lint typecheck test test-py test-pwa test-e2e url check types dev build smoke smoke-real seed-demo deploy deploy-infra deploy-pwa clean

install:
	uv sync --all-extras
	cd pwa && npm ci || cd pwa && npm install
	cd pwa && npx playwright install chromium

fmt:
	uv run ruff format .
	uv run ruff check --fix-only .
	cd pwa && npx prettier --write 'src/**/*.{ts,tsx,css,html}'

fmt-check:
	uv run ruff format --check .
	cd pwa && npx prettier --check 'src/**/*.{ts,tsx,css,html}'

lint:
	uv run ruff check .
	cd pwa && npx eslint 'src/**/*.{ts,tsx}' --max-warnings 0

typecheck:
	uv run mypy
	cd pwa && npx tsc --noEmit

test-py:
	uv run pytest --cov

test-pwa:
	cd pwa && npx vitest run

test: test-py test-pwa

url:
	@aws cloudformation describe-stacks --stack-name MorningDigest \
	  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
	  --output text

test-e2e:
	@outputs=$$(aws cloudformation describe-stacks --stack-name MorningDigest \
	  --query "Stacks[0].Outputs" --output json) && \
	live_url=$$(echo "$$outputs" | python3 -c "import sys,json; o={x['OutputKey']:x['OutputValue'] for x in json.load(sys.stdin)}; print(o['CloudFrontUrl'])") && \
	auth_token=$$(aws ssm get-parameter --name /morning-digest/auth-token --with-decryption --query Parameter.Value --output text) && \
	LIVE_URL=$$live_url AUTH_TOKEN=$$auth_token npm --prefix pwa run test:e2e

check: fmt-check lint typecheck test

types:
	uv run python -c "from server.app import app; import json,sys; json.dump(app.openapi(), sys.stdout)" > /tmp/openapi.json
	cd pwa && npx openapi-typescript /tmp/openapi.json -o src/api-types.ts

dev:
	@echo ">> uvicorn (http://127.0.0.1:8000) + vite (http://127.0.0.1:5173)"
	@( uv run uvicorn server.app:app --reload --port 8000 & \
	   cd pwa && npm run dev -- --port 5173 & \
	   wait )

build: types
	cd pwa && npm run build
	rm -rf server/static
	mkdir -p server/static
	cp -r pwa/dist/* server/static/

smoke:
	uv run pytest tests/integration/test_runner.py -q

smoke-real:
	@test -n "$$CLAUDE_CODE_OAUTH_TOKEN" || (echo "CLAUDE_CODE_OAUTH_TOKEN not set" && exit 1)
	uv run python -m digest.runner --data-dir ./data

seed-demo:
	uv run python scripts/seed_demo_data.py --data-dir ./data --force

deploy-infra:
	cd infra && cdk deploy --require-approval never

deploy-pwa: types
	@outputs=$$(aws cloudformation describe-stacks --stack-name MorningDigest \
	  --query "Stacks[0].Outputs" --output json) && \
	bucket=$$(echo "$$outputs" | python3 -c "import sys,json; o={x['OutputKey']:x['OutputValue'] for x in json.load(sys.stdin)}; print(o['FrontendBucketName'])") && \
	dist=$$(echo "$$outputs" | python3 -c "import sys,json,re; o={x['OutputKey']:x['OutputValue'] for x in json.load(sys.stdin)}; print(o['DistributionId'] if 'DistributionId' in o else re.search(r'--distribution-id (\S+)', o['FrontendDeployCmd']).group(1))") && \
	auth_token=$$(aws ssm get-parameter --name /morning-digest/auth-token --with-decryption --query Parameter.Value --output text) && \
	test -n "$$bucket" || (echo "MorningDigest stack not deployed yet — run 'make deploy-infra' first" && exit 1) && \
	cd pwa && VITE_AUTH_TOKEN=$$auth_token npm run build && cd .. && \
	rm -rf server/static && mkdir -p server/static && cp -r pwa/dist/* server/static/ && \
	aws s3 sync pwa/dist/ s3://$$bucket/ --delete \
	  --exclude "index.html" --exclude "sw.js" --exclude "manifest.webmanifest" \
	  --cache-control "public, max-age=31536000, immutable" && \
	aws s3 cp pwa/dist/index.html s3://$$bucket/index.html \
	  --cache-control "no-cache, must-revalidate" && \
	aws s3 cp pwa/dist/sw.js s3://$$bucket/sw.js \
	  --cache-control "no-cache, must-revalidate" && \
	aws s3 cp pwa/dist/manifest.webmanifest s3://$$bucket/manifest.webmanifest \
	  --cache-control "no-cache, must-revalidate" && \
	aws cloudfront create-invalidation --distribution-id $$dist --paths '/*'

deploy: deploy-infra deploy-pwa

clean:
	rm -rf .venv .venv.nosync pwa/node_modules pwa/dist server/static infra/cdk.out
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
