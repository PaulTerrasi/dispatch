from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digest.s3_store import S3Store
    from digest.store import Store


def data_dir() -> Path:
    """Where the data/ tree lives. Override with $MORNING_DIGEST_DATA_DIR."""
    p = os.environ.get("MORNING_DIGEST_DATA_DIR")
    if p:
        return Path(p).expanduser().resolve()
    return Path.cwd() / "data"


def static_dir() -> Path:
    """Where the built PWA bundle lives (after `make build`). Local dev only."""
    p = os.environ.get("MORNING_DIGEST_STATIC_DIR")
    if p:
        return Path(p).expanduser().resolve()
    return Path(__file__).parent / "static"


def s3_bucket() -> str:
    """S3 bucket name for data storage. Required in AWS deployments."""
    b = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    if not b:
        raise RuntimeError("MORNING_DIGEST_S3_BUCKET is not set")
    return b


def auth_token() -> str | None:
    """Bearer token for API auth. None means auth is disabled (local dev)."""
    return os.environ.get("MORNING_DIGEST_AUTH_TOKEN")


def make_store() -> Store | S3Store:
    """Return S3Store when running in AWS, filesystem Store for local dev."""
    bucket = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    if bucket:
        from digest.s3_store import S3Store

        return S3Store(bucket)
    from digest.store import Store

    return Store(data_dir())


@functools.cache
def _read_ssm(param_name: str) -> str:
    """Read a SecureString from SSM. Cached for the Lambda execution lifetime."""
    import boto3

    client = boto3.client("ssm")
    result = client.get_parameter(Name=param_name, WithDecryption=True)
    return str(result["Parameter"]["Value"])


def resolve_ssm_env_vars() -> None:
    """Resolve any env vars whose value is an SSM path (starts with '/').

    Called once at Lambda/Fargate cold start. Env vars like
    CLAUDE_CODE_OAUTH_TOKEN=/morning-digest/claude-oauth-token are resolved
    in-place so the Claude SDK and middleware can read them normally.

    NYT_COOKIES is optional — the SSM param may not exist yet (it's populated
    out-of-band from a local cookie-refresh job). Treat a missing param as
    empty rather than crashing the cold start.
    """
    required = ("CLAUDE_CODE_OAUTH_TOKEN", "MORNING_DIGEST_AUTH_TOKEN")
    optional = ("NYT_COOKIES",)
    for key in required:
        val = os.environ.get(key, "")
        if val.startswith("/"):
            os.environ[key] = _read_ssm(val)
    for key in optional:
        val = os.environ.get(key, "")
        if val.startswith("/"):
            try:
                os.environ[key] = _read_ssm(val)
            except Exception:
                os.environ[key] = ""
