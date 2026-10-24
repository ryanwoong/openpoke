import asyncio
from uuid import uuid4
from typing import Optional, Union

from fastapi import status
from fastapi.responses import JSONResponse, PlainTextResponse

from ...agents.interaction_agent.runtime import InteractionAgentRuntime
from ...logging_config import logger
from ...models import ChatMessage, ChatRequest
from ...utils import error_response
from .read_checkpoints import read_checkpoints
from ..gmail import get_active_gmail_user_id


# Extract the most recent user message from the chat request payload
def _extract_latest_user_message(payload: ChatRequest) -> Optional[ChatMessage]:
    for message in reversed(payload.messages):
        if message.role.lower().strip() == "user" and message.content.strip():
            return message
    return None


# Process incoming chat requests by routing them to the interaction agent runtime
async def handle_chat_request(payload: ChatRequest) -> Union[PlainTextResponse, JSONResponse]:
    """Handle a chat request using the InteractionAgentRuntime."""

    # Extract user message
    user_message = _extract_latest_user_message(payload)
    if user_message is None:
        return error_response("Missing user message", status_code=status.HTTP_400_BAD_REQUEST)

    user_content = user_message.content.strip()  # Already checked in _extract_latest_user_message

    if payload.retry_of and not read_checkpoints.available(payload.retry_of, prompt=user_content, mailbox=get_active_gmail_user_id() or ""):
        return error_response("Saved search results expired or do not match this request. Start a new search.", status_code=status.HTTP_409_CONFLICT)

    logger.info("chat request", extra={"message_length": len(user_content)})

    try:
        runtime = InteractionAgentRuntime()
    except ValueError as ve:
        # Missing API key error
        logger.error("configuration error", extra={"error": str(ve)})
        return error_response(str(ve), status_code=status.HTTP_400_BAD_REQUEST)

    request_id = payload.request_id or uuid4().hex

    async def _run_interaction() -> None:
        try:
            options = {"retry_of": payload.retry_of} if payload.retry_of else {}
            await runtime.execute(user_message=user_content, request_id=request_id, **options)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("chat task failed", extra={"error": str(exc)})

    asyncio.create_task(_run_interaction())

    return PlainTextResponse("", status_code=status.HTTP_202_ACCEPTED, headers={"X-Request-ID": request_id})
