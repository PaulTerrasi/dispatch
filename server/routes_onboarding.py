"""Onboarding & conversational-feedback API.

Both flows use the Claude Agent SDK with a different system prompt. We expose
a request/response shape over /api/chat/* that the PWA chat view drives turn
by turn.

For the cold-start onboarding, the agent's `propose_profile` tool sends a
markdown draft to the UI; the user confirms or edits, and we POST it to
`/api/onboarding/save` to write `profile.md`.

We keep this minimal: each turn is a fresh `query()` call seeded with the full
conversation so far. No streaming for now (tiny Pi, low traffic).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

import structlog
from claude_agent_sdk import AssistantMessage, TextBlock
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from digest.agent import (
    RunState,
    SdkAgentRunner,
    onboarding_options,
)
from digest.store_protocol import StoreProtocol
from server.deps import get_store

log = structlog.get_logger(__name__)
router = APIRouter()
StoreDep = Annotated[StoreProtocol, Depends(get_store)]


class ChatTurn(BaseModel):
    role: str  # "user" | "assistant"
    text: str


class OnboardingRequest(BaseModel):
    history: list[ChatTurn]


class OnboardingResponse(BaseModel):
    reply: str
    proposed_profile: str | None = None


class SaveProfileRequest(BaseModel):
    markdown: str


def _format_history(history: list[ChatTurn]) -> str:
    lines: list[str] = []
    for turn in history:
        role = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{role}: {turn.text}")
    return "\n".join(lines)


@router.post("/onboarding/message", response_model=OnboardingResponse)
async def onboarding_message(store: StoreDep, body: OnboardingRequest) -> OnboardingResponse:
    if not body.history:
        raise HTTPException(status_code=400, detail="history must be non-empty")
    if body.history[-1].role != "user":
        raise HTTPException(status_code=400, detail="last turn must be from user")

    state = RunState(store=store, today=datetime.now(UTC).date())

    options = onboarding_options(state)
    runner = SdkAgentRunner()
    reply_text = ""

    prompt = (
        "Here is the conversation so far. Produce the next assistant message. "
        "If you have enough information for a draft profile, also call "
        "`propose_profile` with the markdown.\n\n" + _format_history(body.history)
    )

    try:
        async for msg in runner.run(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        reply_text += block.text
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("onboarding agent failed")
        raise HTTPException(status_code=502, detail=f"agent error: {e}") from e

    return OnboardingResponse(
        reply=reply_text.strip(),
        proposed_profile=state.proposed_profile,
    )


class ProfileStatus(BaseModel):
    has_profile: bool


@router.get("/profile/status", response_model=ProfileStatus)
def profile_status(store: StoreDep) -> ProfileStatus:
    return ProfileStatus(has_profile=not store.is_profile_empty())


@router.post("/onboarding/save")
def save_profile(store: StoreDep, body: SaveProfileRequest) -> dict[str, str]:
    text = body.markdown.strip()
    if not text:
        raise HTTPException(status_code=400, detail="markdown is empty")
    store.write_profile(text)
    try:
        store.git_init_if_needed()
        store.git_commit_all("onboarding: initial profile")
    except Exception:  # pragma: no cover
        log.exception("onboarding.commit_failed")
    return {"status": "ok"}
