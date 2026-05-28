"""
Orchestrator — the agentic loop.

Uses Claude's native tool_use to call each skill in sequence:
  safety_check → triage → policy_lookup → finalize_decision

Claude decides which tool to call next based on previous results.
The loop runs until Claude emits a finalize_decision tool call.

Pattern: Tool Registry + Agentic Loop
  1. Register skill functions in TOOL_REGISTRY keyed by tool name
  2. Send tools list + ticket context to Claude via shared LLM client
  3. Claude emits tool_use block → execute skill → feed tool_result
  4. Repeat until Claude calls finalize_decision
"""
import json
from typing import Any

import structlog

from agent.llm_client import call_llm_tools
from agent.models import (
    AgentAction, AgentDecision, JiraTicket, ReasonCode, TicketResult
)
from agent.prompts.orchestrator import SYSTEM, TOOLS
from agent.skills import jira_actions, policy_rag, safety, triage

log = structlog.get_logger(__name__)


# ── Tool Registry ─────────────────────────────────────────────────────────
# Maps Claude tool names → Python callables that execute the skill.
# Adding a new skill = add one entry here + one schema in prompts/orchestrator.py

def _run_safety(inputs: dict) -> dict:
    result = safety.run(
        key=inputs["key"],
        summary=inputs["summary"],
        description=inputs.get("description", ""),
    )
    return result.model_dump()


def _run_triage(inputs: dict) -> dict:
    result = triage.run(
        key=inputs["key"],
        summary=inputs["summary"],
        description=inputs.get("description", ""),
        issue_type=inputs.get("issue_type", "Service Request"),
        priority=inputs.get("priority", "Medium"),
    )
    return result.model_dump()


def _run_policy_lookup(inputs: dict) -> dict:
    result = policy_rag.run(
        key=inputs["key"],
        summary=inputs["summary"],
        description=inputs.get("description", ""),
    )
    return result.model_dump()


def _run_finalize(inputs: dict) -> dict:
    # Just echoes the decision back — processed after loop ends
    return inputs


TOOL_REGISTRY: dict[str, Any] = {
    "safety_check":      _run_safety,
    "triage":            _run_triage,
    "policy_lookup":     _run_policy_lookup,
    "finalize_decision": _run_finalize,
}


# ── Agentic loop ──────────────────────────────────────────────────────────

MAX_TURNS = 8   # safety ceiling — prevents infinite loops


def _build_initial_message(ticket: JiraTicket) -> str:
    return (
        f"Process this Jira ticket through the full pipeline.\n\n"
        f"KEY: {ticket.key}\n"
        f"TYPE: {ticket.issue_type}\n"
        f"PRIORITY: {ticket.priority}\n"
        f"SUMMARY: {ticket.summary}\n"
        f"DESCRIPTION:\n{ticket.description or '(empty)'}"
    )


def _extract_decision(final_inputs: dict) -> AgentDecision:
    """Convert finalize_decision tool inputs into an AgentDecision."""
    action_str = final_inputs.get("action", "DEFER")
    action = AgentAction(action_str)

    reason_raw = final_inputs.get("reason_code")
    try:
        reason_code = ReasonCode(reason_raw) if reason_raw else None
    except ValueError:
        reason_code = ReasonCode.LOW_CONFIDENCE

    return AgentDecision(
        action=action,
        answer=final_inputs.get("answer"),
        policy_citation=final_inputs.get("policy_citation"),
        reason_code=reason_code,
        reason_detail=final_inputs.get("reason_detail"),
        confidence=float(final_inputs.get("confidence", 0.5)),
    )


def run(ticket: JiraTicket, dry_run: bool = False) -> TicketResult:
    """
    Run the full agentic pipeline for a single Jira ticket.

    dry_run=True → runs the full pipeline but skips writing to Jira.
    Returns a TicketResult with the final AgentDecision.
    """
    log.info("orchestrator_start", key=ticket.key, summary=ticket.summary[:60])

    messages: list[dict] = [
        {"role": "user", "content": _build_initial_message(ticket)}
    ]

    final_decision: AgentDecision | None = None
    _chunks_used: list[str] = []   # captured from policy_lookup result
    turns = 0

    # ── Agentic loop ──────────────────────────────────────────────────────
    while turns < MAX_TURNS:
        turns += 1
        log.debug("orchestrator_turn", key=ticket.key, turn=turns)

        response = call_llm_tools(
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            max_tokens=1024,
        )

        # Append Claude's response to message history
        messages.append({"role": "assistant", "content": response.content})

        # ── Check stop condition ──────────────────────────────────────────
        if response.stop_reason == "end_turn":
            log.warning("orchestrator_unexpected_end_turn", key=ticket.key)
            final_decision = AgentDecision(
                action=AgentAction.DEFER,
                reason_code=ReasonCode.LOW_CONFIDENCE,
                reason_detail="Orchestrator ended without a decision. Routing to human.",
                confidence=0.0,
            )
            break

        # ── Process all tool_use blocks ───────────────────────────────────
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name   = block.name
            tool_inputs = block.input
            tool_use_id = block.id

            log.info("tool_called", key=ticket.key, tool=tool_name, turn=turns)

            if tool_name not in TOOL_REGISTRY:
                result_content = json.dumps({"error": f"Unknown tool: {tool_name}"})
            else:
                try:
                    result_data    = TOOL_REGISTRY[tool_name](tool_inputs)
                    result_content = json.dumps(result_data)

                    # Capture chunks from policy_lookup so they reach AgentDecision
                    if tool_name == "policy_lookup":
                        _chunks_used = result_data.get("chunks_used", [])

                    # Capture final decision when finalize_decision is called
                    if tool_name == "finalize_decision":
                        final_decision = _extract_decision(tool_inputs)
                        final_decision.chunks_used = _chunks_used

                except Exception as exc:
                    log.error("tool_execution_error", key=ticket.key,
                              tool=tool_name, error=str(exc))
                    result_content = json.dumps({"error": str(exc)})

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     result_content,
            })

        # Feed all tool results back in one message
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        # ── Exit if finalize_decision was called ──────────────────────────
        if final_decision is not None:
            log.info("orchestrator_done", key=ticket.key,
                     action=final_decision.action, turns=turns)
            break

    else:
        # Hit MAX_TURNS ceiling
        log.error("orchestrator_max_turns_exceeded", key=ticket.key)
        final_decision = AgentDecision(
            action=AgentAction.DEFER,
            reason_code=ReasonCode.LOW_CONFIDENCE,
            reason_detail="Agent exceeded maximum pipeline turns. Routing to human.",
            confidence=0.0,
        )

    # ── Write decision back to Jira ───────────────────────────────────────
    result = jira_actions.apply(ticket, final_decision, dry_run=dry_run)
    return result
