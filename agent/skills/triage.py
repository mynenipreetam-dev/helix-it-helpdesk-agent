"""
Triage Skill — Step 2 of the pipeline.

Classifies the ticket: scope, language, ambiguity, multi-part, authorization.
Uses the shared LLM client for consistent, temperature=0 output.
"""
import json

import structlog

from agent.llm_client import call_llm, extract_json
from agent.models import ReasonCode, TriageResult
from agent.prompts import triage as triage_prompts

log = structlog.get_logger(__name__)


def run(
    key: str,
    summary: str,
    description: str,
    issue_type: str = "Service Request",
    priority: str = "Medium",
) -> TriageResult:
    """
    Triage a ticket.
    Returns TriageResult(pass_triage=True) if the ticket should proceed to policy lookup.
    """
    user_msg = triage_prompts.USER_TEMPLATE.format(
        key=key,
        summary=summary,
        description=description or "(empty)",
        issue_type=issue_type,
        priority=priority,
    )

    raw = call_llm(system=triage_prompts.SYSTEM, user=user_msg, max_tokens=256)

    try:
        data = json.loads(extract_json(raw))
        code_str = data.get("reason_code")
        result = TriageResult(
            pass_triage=bool(data.get("pass_triage", False)),
            reason_code=ReasonCode(code_str) if code_str else None,
            detail=data.get("detail"),
            language=data.get("language", "en"),
            is_multi_part=bool(data.get("is_multi_part", False)),
        )
    except Exception:
        log.warning("triage_parse_error", key=key, raw=raw[:120])
        result = TriageResult(
            pass_triage=False,
            reason_code=ReasonCode.AMBIGUOUS,
            detail="Triage response could not be parsed. Routing to human.",
        )

    if result.pass_triage:
        log.info("triage_passed", key=key, language=result.language)
    else:
        log.info("triage_deferred", key=key, code=result.reason_code)

    return result
