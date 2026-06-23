from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from digest.store_protocol import StoreProtocol
from server.deps import get_store

router = APIRouter()
log = structlog.get_logger(__name__)
StoreDep = Annotated[StoreProtocol, Depends(get_store)]


class ThumbFeedback(BaseModel):
    item_id: str
    value: Literal["up", "down", "none"]
    notes: str | None = None


class ChatFeedback(BaseModel):
    text: str


# How long to batch feedback before draining it into a reflection run. The
# first feedback event opens the window; everything that arrives before it fires
# is folded into the same drain, collapsing a burst of N feedbacks into one
# Fargate task instead of N.
_REFLECT_DEBOUNCE = timedelta(minutes=10)


def _schedule_reflection() -> None:
    """Debounce reflection behind a one-time EventBridge schedule.

    The first feedback event creates a schedule that fires `--reflect-drain`
    ~10 minutes later; feedback arriving while that window is open hits a
    ConflictException (the schedule already exists) and is simply folded into
    the pending drain — `reflect_drain` processes every event accumulated since
    the reflection cursor, so nothing is lost. The schedule deletes itself after
    it fires (ActionAfterCompletion=DELETE), so the next feedback opens a fresh
    window. Falls back to a no-op when the required env vars aren't set (local
    dev / tests)."""
    cluster = os.environ.get("MORNING_DIGEST_REFLECT_CLUSTER")
    task_def = os.environ.get("MORNING_DIGEST_REFLECT_TASK_DEF")
    subnets_raw = os.environ.get("MORNING_DIGEST_REFLECT_SUBNETS")
    bucket = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    role_arn = os.environ.get("MORNING_DIGEST_REFLECT_SCHEDULER_ROLE_ARN")
    schedule_name = os.environ.get("MORNING_DIGEST_REFLECT_SCHEDULE_NAME")
    if not (cluster and task_def and subnets_raw and bucket and role_arn and schedule_name):
        log.info("reflection.skipped", reason="scheduler_env_missing")
        return
    subnets = [s for s in subnets_raw.split(",") if s]
    # One-time `at()` expression in UTC; Scheduler has ~1 minute granularity,
    # which is fine for a 10-minute debounce window.
    fire_at = (datetime.now(UTC) + _REFLECT_DEBOUNCE).strftime("%Y-%m-%dT%H:%M:%S")
    # PascalCase: this is the input to the ECS RunTask SDK call the universal
    # target makes. FARGATE_SPOT mirrors the curation schedule to keep cost low.
    target_input = json.dumps(
        {
            "Cluster": cluster,
            "TaskDefinition": task_def,
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {
                    "Subnets": subnets,
                    "AssignPublicIp": "ENABLED",
                }
            },
            "CapacityProviderStrategy": [
                {"CapacityProvider": "FARGATE_SPOT", "Weight": 1},
                {"CapacityProvider": "FARGATE", "Weight": 0, "Base": 0},
            ],
            "Overrides": {
                "ContainerOverrides": [
                    {
                        "Name": "Runner",
                        "Command": [
                            "python",
                            "-m",
                            "digest.runner",
                            "--reflect-drain",
                            "--bucket",
                            bucket,
                        ],
                    }
                ]
            },
        }
    )
    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("scheduler")
        try:
            client.create_schedule(
                Name=schedule_name,
                ScheduleExpression=f"at({fire_at})",
                ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE",
                Target={
                    "Arn": "arn:aws:scheduler:::aws-sdk:ecs:runTask",
                    "RoleArn": role_arn,
                    "Input": target_input,
                    "RetryPolicy": {
                        "MaximumRetryAttempts": 2,
                        "MaximumEventAgeInSeconds": 600,
                    },
                },
            )
            log.info("reflection.window_opened", fire_at=fire_at)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConflictException":
                # A window is already open — this feedback rides along with it.
                log.info("reflection.window_already_open")
            else:
                raise
    except Exception:
        log.exception("reflection.schedule_failed")


@router.post("/feedback")
def post_feedback(store: StoreDep, body: ThumbFeedback) -> dict[str, str]:
    found_in: date | None = None
    for d in store.list_digests():
        digest = store.read_digest(d)
        if not digest:
            continue
        if any(i.get("id") == body.item_id for i in digest.get("items", []) or []):
            value: str | None = None if body.value == "none" else body.value
            if store.update_item_feedback(d, body.item_id, value):
                found_in = d
                break
    if found_in is None:
        raise HTTPException(status_code=404, detail="item not found in any digest")
    event: dict[str, Any] = {
        "item_id": body.item_id,
        "kind": "thumb",
        "value": body.value,
        "digest_date": found_in.isoformat(),
    }
    if body.notes:
        event["notes"] = body.notes
    store.append_feedback(event)
    _schedule_reflection()
    return {"status": "ok"}


@router.post("/chat")
def post_chat(store: StoreDep, body: ChatFeedback) -> dict[str, str]:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")
    store.append_feedback({"kind": "chat", "text": text})
    _schedule_reflection()
    return {"status": "ok"}
