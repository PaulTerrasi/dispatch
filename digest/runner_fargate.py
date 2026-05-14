"""ECS Fargate task entry point. EventBridge Scheduler starts this container hourly.

The hourly invocation runs curation and then drains any pending feedback events
into reflection runs. The drain is a safety net: feedback POSTs in the API
Lambda also fire-and-forget an ECS task to drain immediately, but those calls
can fail transiently (capacity, deploy-window races, Spot interruptions). The
scheduled drain guarantees pending feedback is processed within an hour even
if every fire-and-forget trigger fails.

Both phases are resumable through durable S3 state (the feedback log + the
reflection cursor + a TTL'd lock), so a SIGTERM here — e.g. a FARGATE_SPOT
interruption — never loses pending work; we just want to release the
reflection lock promptly on the way out so the next drain doesn't wait for
the 15-minute TTL.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

import structlog

from digest.runner import _configure_logging, reflect_drain, run_once
from digest.s3_store import S3Store
from server.config import resolve_ssm_env_vars

log = structlog.get_logger(__name__)


async def _run(bucket: str) -> int:
    main_task = asyncio.current_task()
    assert main_task is not None
    loop = asyncio.get_running_loop()

    def _on_signal(signame: str) -> None:
        log.warning("fargate.signal_received", signal=signame)
        main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal, sig.name)

    summary = await run_once(bucket=bucket)
    print(json.dumps(summary.__dict__, default=str, indent=2))

    # Safety-net drain: pending feedback events are durable in S3, but the
    # only other thing that drains them is a fire-and-forget call from the
    # API Lambda. Running it here ensures pending events are never stuck.
    store = S3Store(bucket)
    try:
        await reflect_drain(store=store)
    except Exception:
        log.exception("fargate.reflect_drain_failed")

    return 0 if summary.exit_reason != "error" else 1


def main() -> None:
    _configure_logging()
    resolve_ssm_env_vars()  # resolve SSM paths before the Claude SDK reads env vars

    bucket = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    if not bucket:
        print("ERROR: MORNING_DIGEST_S3_BUCKET is not set", file=sys.stderr)
        sys.exit(1)

    try:
        exit_code = asyncio.run(_run(bucket))
    except asyncio.CancelledError:
        # SIGTERM/SIGINT propagated; reflect_drain's finally block has already
        # released the reflection lock if one was held.
        log.warning("fargate.cancelled")
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
