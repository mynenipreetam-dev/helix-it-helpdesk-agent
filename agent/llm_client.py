"""
Shared Anthropic LLM client for the Helix agent.

All LLM calls in the project go through this module so that:
  - API key and model are sourced from settings in one place
  - temperature=0 is enforced globally for deterministic, consistent output
  - JSON extraction is robust (handles fences, trailing prose, brace matching)
  - A single re-usable `call_llm` helper reduces boilerplate in every skill

Usage
-----
    from agent.llm_client import call_llm, call_llm_tools, extract_json

    # Simple text completion (skills: safety, triage, policy_rag)
    raw = call_llm(system=SYSTEM, user=user_msg, max_tokens=256)
    data = extract_json(raw)

    # Tool-use agentic loop (orchestrator)
    response = call_llm_tools(tools=TOOLS, messages=messages, max_tokens=1024)
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import anthropic
import structlog

from agent.config import settings

log = structlog.get_logger(__name__)


# ── Singleton client ───────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    """Return a cached Anthropic client. Created once per process."""
    log.info("llm_client_init", model=settings.claude_model, temperature=settings.llm_temperature)
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ── JSON extraction ────────────────────────────────────────────────────────

def extract_json(raw: str) -> str:
    """
    Robustly extract the first JSON object from a raw LLM response.

    Handles:
      - Clean JSON output
      - ```json ... ``` or ``` ... ``` fences
      - Trailing markdown / prose after the closing brace
      - Escaped characters inside string values
      - Leading/trailing whitespace

    Returns the extracted JSON string ready for json.loads().
    Raises ValueError if no JSON object is found.
    """
    text = raw.strip()

    # Strip opening fence (```json or ```)
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    # Strip closing fence and anything after it
    text = re.sub(r"\n?```.*$", "", text, flags=re.DOTALL)
    text = text.strip()

    # Walk the string to find the first complete { ... } object
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM response: {text[:120]!r}")

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]

    raise ValueError(f"Unbalanced braces in LLM response: {text[:120]!r}")


# ── Simple text completion ─────────────────────────────────────────────────

def call_llm(
    *,
    system: str,
    user: str,
    max_tokens: int = 512,
) -> str:
    """
    Call Claude for a single-turn text completion.

    Uses temperature=0 from settings for deterministic output.
    Returns the raw text of the first content block.
    """
    client = get_client()
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        temperature=settings.llm_temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


# ── Tool-use agentic loop call ─────────────────────────────────────────────

def call_llm_tools(
    *,
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
) -> anthropic.types.Message:
    """
    Call Claude with tool_use enabled (for the orchestrator agentic loop).

    temperature is intentionally omitted for tool-use calls — the Anthropic API
    does not support temperature on tool-use requests (it raises a validation error).
    Returns the full Message object so the caller can inspect stop_reason and content.
    """
    client = get_client()
    return client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system,
        tools=tools,
        messages=messages,
    )
