"""
Jira Actions Skill — Final step.

Writes the agent decision back to Jira:
  RESOLVE → grounded comment + 'agent-resolved' label + transition to Done
  DEFER   → reason-code comment + 'needs-human' label + leave open

Also handles:
  - Fetching open tickets (batch polling)
  - Parsing webhook payloads into JiraTicket objects
"""
import structlog
from jira import JIRA, JIRAError
from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import settings
from agent.models import AgentAction, AgentDecision, JiraTicket, TicketResult

log = structlog.get_logger(__name__)

# ── Jira client singleton ─────────────────────────────────────────────────

_jira: JIRA | None = None


def _client() -> JIRA:
    global _jira
    if _jira is None:
        _jira = JIRA(
            server=settings.jira_base_url,
            basic_auth=(settings.jira_user_email, settings.jira_api_token),
        )
        log.info("jira_connected", server=settings.jira_base_url)
    return _jira


# ── Comment templates ─────────────────────────────────────────────────────

_RESOLVE_TMPL = """\
✅ *Auto-resolved by Helix IT Policy Agent*

{answer}

---
*Policy citation:* `{citation}`
*Confidence:* {confidence:.0%}

_If this doesn't fully address your issue, reopen this ticket or contact the IT Service Desk._
"""

_DEFER_TMPL = """\
🔁 *Routed to human agent by Helix IT Policy Agent*

*Reason:* `{reason_code}`
{detail}

---
_A member of the IT Service Desk will follow up shortly._
"""


# ── Jira write helpers ────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
def _post_comment(issue_key: str, body: str) -> None:
    _client().add_comment(issue_key, body)
    log.info("comment_posted", key=issue_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
def _add_label(issue_key: str, label: str) -> None:
    issue = _client().issue(issue_key)
    existing = list(issue.fields.labels or [])
    if label not in existing:
        issue.update(fields={"labels": existing + [label]})
        log.info("label_added", key=issue_key, label=label)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
def _transition_done(issue_key: str) -> None:
    transitions = _client().transitions(issue_key)
    done = next(
        (t for t in transitions if t["name"].lower() in ("done", "resolve", "closed")),
        None,
    )
    if done:
        _client().transition_issue(issue_key, done["id"])
        log.info("transitioned_done", key=issue_key)
    else:
        log.warning("no_done_transition", key=issue_key,
                    available=[t["name"] for t in transitions])


# ── Ticket fetching ───────────────────────────────────────────────────────

def _extract_description(desc) -> str:
    """Handle ADF (dict) and plain-text descriptions."""
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict) and "content" in desc:
        parts = []
        for block in desc.get("content", []):
            for node in block.get("content", []):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
        return "\n".join(parts)
    return str(desc)


def fetch_tickets(label: str = "eval-set", max_results: int = 50, status: str = "To Do") -> list[JiraTicket]:
    """Fetch open tickets from Jira matching project + optional label."""
    label_clause = f' AND labels = "{label}"' if label else ""
    jql = (
        f'project = "{settings.jira_project_key}" '
        f'AND status = "{status}"'
        f"{label_clause} "
        f"ORDER BY created ASC"
    )
    log.info("fetching_tickets", jql=jql)
    issues = _client().search_issues(
        jql, maxResults=max_results,
        fields="summary,description,issuetype,priority,labels,status"
    )
    tickets = []
    for issue in issues:
        f = issue.fields
        tickets.append(JiraTicket(
            key=issue.key,
            summary=f.summary or "",
            description=_extract_description(f.description),
            issue_type=f.issuetype.name if f.issuetype else "Task",
            priority=f.priority.name if f.priority else "Medium",
            labels=list(f.labels or []),
            status=f.status.name if f.status else "To Do",
        ))
    log.info("tickets_fetched", count=len(tickets))
    return tickets


def parse_webhook(payload: dict) -> JiraTicket | None:
    """Parse a Jira webhook payload into a JiraTicket. Returns None if not actionable."""
    event = payload.get("webhookEvent", "")
    if event not in ("jira:issue_created", "jira:issue_updated"):
        return None

    issue = payload.get("issue", {})
    fields = issue.get("fields", {})

    if fields.get("project", {}).get("key") != settings.jira_project_key:
        return None

    labels = list(fields.get("labels") or [])
    # Skip tickets already actioned
    if "agent-resolved" in labels or "needs-human" in labels:
        return None

    return JiraTicket(
        key=issue.get("key", ""),
        summary=fields.get("summary", ""),
        description=_extract_description(fields.get("description")),
        issue_type=(fields.get("issuetype") or {}).get("name", "Task"),
        priority=(fields.get("priority") or {}).get("name", "Medium"),
        labels=labels,
        status=(fields.get("status") or {}).get("name", "To Do"),
    )


# ── Public entry point ────────────────────────────────────────────────────

def apply(ticket: JiraTicket, decision: AgentDecision, dry_run: bool = False) -> TicketResult:
    """
    Write the agent decision back to Jira.
    dry_run=True computes the result but skips all Jira writes.
    """
    result = TicketResult(
        ticket_key=ticket.key,
        ticket_summary=ticket.summary,
        decision=decision,
    )

    if dry_run:
        log.info("dry_run_skip_write", key=ticket.key, action=decision.action)
        return result

    try:
        if decision.action == AgentAction.RESOLVE:
            body = _RESOLVE_TMPL.format(
                answer=decision.answer or "",
                citation=decision.policy_citation or "N/A",
                confidence=decision.confidence,
            )
            _post_comment(ticket.key, body)
            _add_label(ticket.key, "agent-resolved")
            _transition_done(ticket.key)
            result.comment_posted = True
            result.label_applied = "agent-resolved"

        else:  # DEFER
            body = _DEFER_TMPL.format(
                reason_code=decision.reason_code.value if decision.reason_code else "UNKNOWN",
                detail=decision.reason_detail or "",
            )
            _post_comment(ticket.key, body)
            _add_label(ticket.key, "needs-human")
            result.comment_posted = True
            result.label_applied = "needs-human"

    except JIRAError as e:
        log.error("jira_write_error", key=ticket.key, status=e.status_code, text=e.text)
        result.error = str(e)

    return result
