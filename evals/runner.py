"""
DeepEval eval runner.

Runs the agent against all 50 ground truth tickets.
NEVER writes to Jira by default — tickets are NOT modified during eval.
Outputs a CSV report and prints a summary table.

Usage:
    poetry run python evals/runner.py                  # safe — no Jira writes (default)
    poetry run python evals/runner.py --ticket KAN-4   # single ticket, still no Jira writes
    poetry run python evals/runner.py --live           # WARNING: writes comments+labels to Jira
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deepeval import evaluate
from deepeval.test_case import LLMTestCase

from agent.models import AgentAction, JiraTicket
from agent.orchestrator import run as agent_run
from agent.skills.jira_actions import fetch_tickets
from evals.ground_truth import EVAL_CASES, EvalCase
from evals.metrics import (
    ActionAccuracyMetric,
    CitationGroundednessMetric,
    ReasonCodeAccuracyMetric,
)


def _ticket_from_jira(jira_key: str, tickets: list[JiraTicket]) -> JiraTicket | None:
    return next((t for t in tickets if t.key == jira_key), None)


def _make_test_case(case: EvalCase, result) -> LLMTestCase:
    """Convert an agent TicketResult into a DeepEval LLMTestCase."""
    d = result.decision

    actual_action = d.action.value if d.action else "DEFER"
    actual_citation   = d.policy_citation or ""
    actual_reason     = d.reason_code.value if d.reason_code else ""

    return LLMTestCase(
        input=case.summary,
        actual_output=actual_action,
        expected_output=case.expected_action,
        retrieval_context=d.chunks_used or [],
        additional_metadata={
            "ticket_id":           case.ticket_id,
            "jira_key":            case.jira_key,
            "policy_citation":     actual_citation,
            "expected_citation":   case.expected_citation or "",
            "extra_citations":     case.extra_citations or [],   # accepted multi-section extras
            "actual_reason_code":  actual_reason,
            "expected_reason_code": case.expected_reason or "",
            "confidence":          d.confidence,
        },
    )


def run_eval(filter_key: str | None = None, dry_run: bool = True) -> None:
    print("\n🔍 Fetching tickets from Jira…")
    jira_tickets = fetch_tickets(label="eval-set", max_results=100, status="To Do")
    ticket_map = {t.key: t for t in jira_tickets}

    cases = EVAL_CASES
    if filter_key:
        cases = [c for c in cases if c.jira_key == filter_key]
        if not cases:
            print(f"No eval case found for key: {filter_key}")
            return

    print(f"Running {len(cases)} eval cases…\n")

    test_cases: list[LLMTestCase] = []
    csv_rows: list[dict] = []

    metrics = [
        ActionAccuracyMetric(),
        CitationGroundednessMetric(),
        ReasonCodeAccuracyMetric(),
    ]

    for i, case in enumerate(cases, 1):
        ticket = ticket_map.get(case.jira_key)
        if not ticket:
            # Ticket not in Jira "To Do" — may already be actioned; build from ground truth
            ticket = JiraTicket(
                key=case.jira_key,
                summary=case.summary,
                description="",
            )

        print(f"[{i:02d}/{len(cases)}] {case.jira_key} — {case.summary[:55]}…")
        start = time.time()

        try:
            result = agent_run(ticket, dry_run=dry_run)
            elapsed = round(time.time() - start, 2)
            d = result.decision

            tc = _make_test_case(case, result)
            test_cases.append(tc)

            action_ok   = d.action.value == case.expected_action

            # Citation check: partial match against primary citation OR any accepted extra
            if case.expected_action == "RESOLVE":
                agent_cit_norm = " ".join((d.policy_citation or "").lower().split())
                accepted = [case.expected_citation or ""] + (case.extra_citations or [])
                citation_ok = any(
                    " ".join(exp.lower().split()) in agent_cit_norm
                    for exp in accepted if exp
                )
            else:
                citation_ok = True

            reason_ok  = (d.reason_code.value if d.reason_code else "") == (case.expected_reason or "") \
                         if case.expected_action == "DEFER" else True

            status = "✅" if (action_ok and citation_ok and reason_ok) else "❌"
            print(f"        {status} action={d.action.value} "
                  f"citation={d.policy_citation or '-'} "
                  f"reason={d.reason_code.value if d.reason_code else '-'} "
                  f"conf={d.confidence:.2f} ({elapsed}s)")

            csv_rows.append({
                "ticket_id":          case.ticket_id,
                "jira_key":           case.jira_key,
                "summary":            case.summary,
                "expected_action":    case.expected_action,
                "agent_action":       d.action.value,
                "action_correct":     action_ok,
                "expected_citation":  case.expected_citation or "",
                "agent_citation":     d.policy_citation or "",
                "citation_correct":   citation_ok,
                "expected_reason":    case.expected_reason or "",
                "agent_reason":       d.reason_code.value if d.reason_code else "",
                "reason_correct":     reason_ok,
                "confidence":         round(d.confidence, 3),
                "overall_correct":    action_ok and citation_ok and reason_ok,
                "elapsed_s":          elapsed,
                "error":              result.error or "",
            })

        except Exception as e:
            print(f"        💥 ERROR: {e}")
            csv_rows.append({
                "ticket_id": case.ticket_id, "jira_key": case.jira_key,
                "summary": case.summary, "error": str(e),
                "overall_correct": False,
            })

    # ── DeepEval evaluate ─────────────────────────────────────────────────
    print("\n📊 Running DeepEval metrics…")
    evaluate(test_cases=test_cases, metrics=metrics)

    # ── CSV report ────────────────────────────────────────────────────────
    report_path = "eval_report.csv"
    with open(report_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys() if csv_rows else [])
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"\n📄 CSV report saved → {report_path}")

    # ── Summary ───────────────────────────────────────────────────────────
    total   = len(csv_rows)
    correct = sum(1 for r in csv_rows if r.get("overall_correct"))
    resolves = [r for r in csv_rows if r.get("expected_action") == "RESOLVE"]
    defers   = [r for r in csv_rows if r.get("expected_action") == "DEFER"]

    print(f"\n{'='*50}")
    print(f"  Total:          {total}")
    print(f"  Correct:        {correct}/{total} ({correct/total*100:.1f}%)")
    print(f"  RESOLVE acc:    {sum(1 for r in resolves if r.get('action_correct'))}/{len(resolves)}")
    print(f"  DEFER acc:      {sum(1 for r in defers if r.get('action_correct'))}/{len(defers)}")
    print(f"  False positives (resolved when should defer): "
          f"{sum(1 for r in defers if r.get('agent_action') == 'RESOLVE')}")
    print(f"  False negatives (deferred when should resolve): "
          f"{sum(1 for r in resolves if r.get('agent_action') == 'DEFER')}")
    print(f"{'='*50}\n")

def main():
    parser = argparse.ArgumentParser(description="Run Helix Agent eval")
    parser.add_argument("--ticket", help="Evaluate a single ticket by Jira key (e.g. KAN-4)")
    # dry_run is TRUE by default — DeepEval never touches Jira unless you explicitly pass --live
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Actually write comments/labels to Jira (default: off — eval never modifies tickets)",
    )
    args = parser.parse_args()
    run_eval(filter_key=args.ticket, dry_run=not args.live)

if __name__ == "__main__":
    main()

