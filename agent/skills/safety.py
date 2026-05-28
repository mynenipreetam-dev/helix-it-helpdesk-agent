"""
Safety Skill — Step 1 of the pipeline.

Runs fast deterministic regex checks first (no LLM cost).
Falls back to shared LLM client only for nuanced cases.
"""
import json
import re

import structlog

from agent.llm_client import call_llm, extract_json
from agent.models import ReasonCode, SafetyResult
from agent.prompts import safety as safety_prompts

log = structlog.get_logger(__name__)


# ── Deterministic pre-checks (fast, free) ─────────────────────────────────

_RULES: list[tuple[str, ReasonCode, list[str]]] = [
    (
        "PROMPT_INJECTION",
        ReasonCode.PROMPT_INJECTION,
        [
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"you\s+are\s+now\s+in\s+(admin|god|override)\s+mode",
            r"\[SYSTEM\s+OVERRIDE\]",
            r"disregard\s+your\s+(system\s+)?prompt",
            r"new\s+persona",
            r"jailbreak",
            r"DAN\s+mode",
        ],
    ),
    (
        "CREDENTIAL_EXPOSURE",
        ReasonCode.SENSITIVE_CREDENTIAL_EXPOSURE,
        [
            r"(?:password|passwd|pwd)\s*[:=]\s*\S+",
            r"(?:api[_\-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9\-_]{16,}",
            r"BEGIN\s+(RSA|EC|OPENSSH)\s+PRIVATE\s+KEY",
            r"eyJ[A-Za-z0-9_\-]{20,}",   # JWT
            r"sk-[A-Za-z0-9]{20,}",       # OpenAI-style key
        ],
    ),
    (
        "HOSTILE_LANGUAGE",
        ReasonCode.HOSTILE_LANGUAGE,
        [
            r"\b(f[u\*]ck|sh[i\*]t|idiot|moron|incompetent|useless)\b",
            r"(sue\s+(you|the\s+company)|escalate\s+to\s+the\s+board)",
        ],
    ),
    (
        "AUTOMATED_ALERT",
        ReasonCode.AUTOMATED_SYSTEM_ALERT,
        [
            r"\[automated\s+alert\]",
            r"alert\s+generated\s+by",
            r"(nagios|pagerduty|datadog|prometheus|grafana)\s+(alert|notification)",
            r"ticket\s+auto[-\s]?created",
        ],
    ),
]


def _regex_check(text: str) -> SafetyResult | None:
    """Return a SafetyResult if a deterministic pattern fires, else None."""
    for _name, code, patterns in _RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return SafetyResult(
                    safe=False,
                    reason_code=code,
                    detail=f"Deterministic pattern matched: {pat[:60]}",
                )
    return None


# ── LLM fallback ──────────────────────────────────────────────────────────

def _llm_check(key: str, summary: str, description: str) -> SafetyResult:
    user_msg = safety_prompts.USER_TEMPLATE.format(
        key=key, summary=summary, description=description or "(empty)"
    )
    raw = call_llm(system=safety_prompts.SYSTEM, user=user_msg, max_tokens=256)
    try:
        data = json.loads(extract_json(raw))
        code_str = data.get("reason_code")
        return SafetyResult(
            safe=bool(data.get("safe", True)),
            reason_code=ReasonCode(code_str) if code_str else None,
            detail=data.get("detail"),
        )
    except Exception:
        log.warning("safety_parse_error", key=key, raw=raw[:120])
        return SafetyResult(safe=True)   # fail open — let triage handle it


# ── Public entry point ─────────────────────────────────────────────────────

def run(key: str, summary: str, description: str) -> SafetyResult:
    """
    Run safety checks on a ticket.
    Returns SafetyResult(safe=True) if the ticket is safe to process.
    """
    text = f"{summary}\n{description}"

    # Fast path — deterministic regex
    result = _regex_check(text)
    if result:
        log.warning("safety_blocked_regex", key=key, code=result.reason_code)
        return result

    # Slow path — LLM for nuanced cases
    result = _llm_check(key, summary, description)
    if not result.safe:
        log.warning("safety_blocked_llm", key=key, code=result.reason_code)
    else:
        log.info("safety_passed", key=key)
    return result
