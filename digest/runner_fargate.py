"""ECS Fargate task entry point. EventBridge Scheduler starts this container daily."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from digest.runner import _configure_logging, run_once
from server.config import resolve_ssm_env_vars


def main() -> None:
    _configure_logging()
    resolve_ssm_env_vars()  # resolve SSM paths before the Claude SDK reads env vars

    bucket = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    if not bucket:
        print("ERROR: MORNING_DIGEST_S3_BUCKET is not set", file=sys.stderr)
        sys.exit(1)

    summary = asyncio.run(run_once(bucket=bucket))
    print(json.dumps(summary.__dict__, default=str, indent=2))
    sys.exit(0 if summary.exit_reason != "error" else 1)


if __name__ == "__main__":
    main()
