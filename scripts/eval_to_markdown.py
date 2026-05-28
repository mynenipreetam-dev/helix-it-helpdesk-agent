"""
Convert eval_report.csv → EVAL_REPORT.md

Usage:
    poetry run python scripts/eval_to_markdown.py
    poetry run python scripts/eval_to_markdown.py --input eval_report.csv --output EVAL_REPORT.md
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_report(rows: list[dict]) -> str:
    # ── Summary stats ──────────────────────────────────────────────────────
    total     = len(rows)
    correct   = sum(1 for r in rows if r.get("overall_correct", "").lower() == "true")
    resolves  = [r for r in rows if r.get("expected_action") == "RESOLVE"]
    defers    = [r for r in rows if r.get("expected_action") == "DEFER"]
    resolve_correct = sum(1 for r in resolves if r.get("action_correct", "").lower() == "true")
    defer_correct   = sum(1 for r in defers  if r.get("action_correct", "").lower() == "true")
    false_pos = sum(1 for r in defers   if r.get("agent_action") == "RESOLVE")
    false_neg = sum(1 for r in resolves if r.get("agent_action") == "DEFER")
    failures  = [r for r in rows if r.get("overall_correct", "").lower() != "true"]

    pct = correct / total * 100 if total else 0

    lines = []

    # ── Header ─────────────────────────────────────────────────────────────
    lines += [
        "# Helix IT Helpdesk Agent — Eval Report",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"> Dataset: {total} tickets (30 RESOLVE · 20 DEFER)  ",
        f"> Model: claude-sonnet-4-5  ",
        f"> Eval framework: DeepEval 4.0.4",
        "",
    ]

    # ── Scorecard ──────────────────────────────────────────────────────────
    lines += [
        "## Scorecard",
        "",
        "| Metric | Result |",
        "|--------|--------|",
        f"| **Overall accuracy** | **{correct}/{total} ({pct:.1f}%)** |",
        f"| RESOLVE accuracy | {resolve_correct}/{len(resolves)} ({resolve_correct/len(resolves)*100:.1f}%) |" if resolves else "| RESOLVE accuracy | N/A |",
        f"| DEFER accuracy | {defer_correct}/{len(defers)} ({defer_correct/len(defers)*100:.1f}%) |" if defers else "| DEFER accuracy | N/A |",
        f"| False positives (resolved when should defer) | {false_pos} |",
        f"| False negatives (deferred when should resolve) | {false_neg} |",
        f"| Failing tickets | {len(failures)} |",
        "",
    ]

    # ── Failures ───────────────────────────────────────────────────────────
    if failures:
        lines += [
            "## Failures",
            "",
            "| Ticket | Jira Key | Summary | Expected | Got | Citation | Reason Code |",
            "|--------|----------|---------|----------|-----|----------|-------------|",
        ]
        for r in failures:
            summary = r.get("summary", "")[:55].replace("|", "\\|")
            lines.append(
                f"| {r.get('ticket_id','')} "
                f"| {r.get('jira_key','')} "
                f"| {summary}… "
                f"| {r.get('expected_action','')} "
                f"| {r.get('agent_action','')} "
                f"| {r.get('agent_citation','-') or '-'} "
                f"| {r.get('agent_reason','-') or '-'} |"
            )
        lines.append("")
    else:
        lines += ["## Failures", "", "_None — all 50 tickets passed._", ""]

    # ── Full results table ─────────────────────────────────────────────────
    lines += [
        "## Full Results",
        "",
        "| # | Ticket | Jira | Summary | Expected | Agent | ✓ | Citation | Reason | Conf |",
        "|---|--------|------|---------|----------|-------|---|----------|--------|------|",
    ]

    for i, r in enumerate(rows, 1):
        ok      = "✅" if r.get("overall_correct", "").lower() == "true" else "❌"
        summary = r.get("summary", "")[:45].replace("|", "\\|")
        conf    = float(r.get("confidence", 0))
        citation = (r.get("agent_citation") or "-").replace("|", "\\|")
        reason   = (r.get("agent_reason")   or "-")
        lines.append(
            f"| {i} "
            f"| {r.get('ticket_id','')} "
            f"| {r.get('jira_key','')} "
            f"| {summary}… "
            f"| {r.get('expected_action','')} "
            f"| {r.get('agent_action','')} "
            f"| {ok} "
            f"| {citation} "
            f"| {reason} "
            f"| {conf:.2f} |"
        )

    lines.append("")

    # ── Metric definitions ─────────────────────────────────────────────────
    lines += [
        "## Metric Definitions",
        "",
        "| Metric | Description |",
        "|--------|-------------|",
        "| **Action Accuracy** | Agent chose RESOLVE vs DEFER correctly |",
        "| **Citation Groundedness** | Cited policy section appears in retrieved ChromaDB chunks (RESOLVE only) |",
        "| **Reason Code Accuracy** | Agent's DEFER reason code matches ground truth (DEFER only) |",
        "",
        "---",
        "_Report generated by `scripts/eval_to_markdown.py`_",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Convert eval_report.csv to Markdown")
    parser.add_argument("--input",  default="eval_report.csv",  help="Path to CSV report")
    parser.add_argument("--output", default="EVAL_REPORT.md",   help="Output Markdown file")
    args = parser.parse_args()

    # Search common locations if the default path doesn't exist
    input_path = Path(args.input)
    if not input_path.exists():
        candidates = [
            Path("eval_report.csv"),
            Path("../eval_report.csv"),
            Path(__file__).parent.parent / "eval_report.csv",
        ]
        for c in candidates:
            if c.exists():
                input_path = c
                break
        else:
            print(f"❌ Could not find eval_report.csv. Run: poetry run python evals/runner.py first.")
            print(f"   Then: poetry run python scripts/eval_to_markdown.py --input <path/to/eval_report.csv>")
            return

    output_path = Path(args.output)

    rows   = load_csv(str(input_path))
    report = build_report(rows)

    output_path.write_text(report, encoding="utf-8")
    print(f"✅ Report written → {output_path}  ({len(rows)} tickets)")


if __name__ == "__main__":
    main()