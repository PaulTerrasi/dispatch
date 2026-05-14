from __future__ import annotations

import os
from datetime import date
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


def _trigger_reflection() -> None:
    """Spawn an ECS Fargate task to run reflection. Fire-and-forget — the task
    runs independently of this Lambda invocation. Falls back to a no-op when the
    required env vars aren't set (local dev / tests)."""
    cluster = os.environ.get("MORNING_DIGEST_REFLECT_CLUSTER")
    task_def = os.environ.get("MORNING_DIGEST_REFLECT_TASK_DEF")
    subnets_raw = os.environ.get("MORNING_DIGEST_REFLECT_SUBNETS")
    bucket = os.environ.get("MORNING_DIGEST_S3_BUCKET")
    if not (cluster and task_def and subnets_raw and bucket):
        log.info("reflection.skipped", reason="ecs_env_missing")
        return
    subnets = [s for s in subnets_raw.split(",") if s]
    try:
        import boto3

        client = boto3.client("ecs")
        client.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "Runner",
                        "command": [
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
            startedBy="feedback-reflection",
        )
        log.info("reflection.triggered", cluster=cluster)
    except Exception:
        log.exception("reflection.trigger_failed")


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
    _trigger_reflection()
    return {"status": "ok"}


@router.post("/chat")
def post_chat(store: StoreDep, body: ChatFeedback) -> dict[str, str]:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")
    store.append_feedback({"kind": "chat", "text": text})
    _trigger_reflection()
    return {"status": "ok"}
