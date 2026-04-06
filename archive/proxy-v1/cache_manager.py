"""Prompt cache manager.

Adds cache_control markers to system prompts and long static message
prefixes so Anthropic can cache them across requests.
"""

from typing import Dict, List

# Minimum system prompt length (chars) to mark cacheable
MIN_CACHE_LENGTH = 1024


def optimize(request_body: Dict) -> Dict:
    """Add cache_control markers to the request body where beneficial.

    Modifies the request in place and returns it.
    """
    _cache_system_prompt(request_body)
    _cache_long_prefix_messages(request_body)
    return request_body


def _cache_system_prompt(body: Dict) -> None:
    """Mark system prompt blocks as cacheable."""
    system = body.get("system")
    if not system:
        return

    # String system prompt -> convert to block format with cache_control
    if isinstance(system, str):
        if len(system) >= MIN_CACHE_LENGTH:
            body["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        return

    # List of blocks -> mark the last block as cacheable
    if isinstance(system, list) and len(system) > 0:
        total_len = sum(
            len(b.get("text", "")) if isinstance(b, dict) else len(str(b))
            for b in system
        )
        if total_len >= MIN_CACHE_LENGTH:
            last = system[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}


def _cache_long_prefix_messages(body: Dict) -> None:
    """Mark long static prefix messages (e.g., first user message with
    large context) as cacheable.

    Only marks the first user message if it's long enough and doesn't
    already have cache_control.
    """
    messages = body.get("messages", [])
    if not messages:
        return

    first = messages[0]
    if first.get("role") != "user":
        return

    content = first.get("content")
    if isinstance(content, str) and len(content) >= MIN_CACHE_LENGTH * 2:
        # Convert to block format
        messages[0]["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif isinstance(content, list):
        total_len = sum(
            len(b.get("text", "")) if isinstance(b, dict) else 0
            for b in content
        )
        if total_len >= MIN_CACHE_LENGTH * 2 and len(content) > 0:
            last = content[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}
