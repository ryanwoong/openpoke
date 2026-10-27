"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List

from ...logging_config import logger
from ...services.execution import get_agent_roster

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()


def build_system_prompt() -> str:
    """Include current integration state in the interaction prompt."""
    from ...services.gmail.client import get_integration_context

    return f"{SYSTEM_PROMPT}\n\n{get_integration_context()}"


def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, bounded routing references, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(f"<recent_agent_dispatches>\n{_render_recent_dispatches()}\n</recent_agent_dispatches>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


def _render_recent_dispatches() -> str:
    try:
        references = get_agent_roster().recent_dispatches()
    except (OSError, ValueError):
        logger.warning("Agent directory unavailable during prompt construction")
        return "Directory unavailable; use find_agents to retry lookup."
    rendered = []
    size = 0
    for reference in references:
        attributes = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in reference.items())
        entry = f"<dispatch {attributes} />"
        if size + len(entry.encode("utf-8")) + 1 <= 8192:
            rendered.append(entry)
            size += len(entry.encode("utf-8")) + 1
    return "\n".join(rendered) or "None"


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
