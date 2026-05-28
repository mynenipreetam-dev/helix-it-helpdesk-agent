"""
Orchestrator system prompt + Claude tool schemas.
The orchestrator uses Claude's native tool_use to call each skill in sequence.
"""

SYSTEM = """\
You are the Orchestrator for the Helix IT Helpdesk Agent.

You manage a pipeline of 3 specialist skill agents. Call them IN ORDER using the tools provided.
Never skip a step. Never answer the ticket yourself.

PIPELINE
--------
Step 1 → safety_check     : Must always run first. If unsafe → stop, return DEFER.
Step 2 → triage           : Only if safety passes. If triage fails → stop, return DEFER.
Step 3 → policy_lookup    : Only if triage passes. Returns RESOLVE or DEFER.

After all steps complete, call finalize_decision with the final AgentDecision.

RULES
-----
- Call tools one at a time, in order.
- Do not interpret or modify skill outputs — pass them through faithfully.
- If any skill returns a DEFER signal, call finalize_decision immediately with that result.
- You are done when finalize_decision has been called exactly once.

FINALIZE DECISION RULES
-----------------------
- RESOLVE: populate action=RESOLVE, answer, policy_citation, confidence. Set reason_code=null, reason_detail=null.
- DEFER: populate action=DEFER, reason_code, reason_detail, confidence. Set answer=null, policy_citation=null.
  NEVER include a policy_citation on a DEFER — a citation implies the agent resolved something, which is wrong.
"""

# ── Claude tool schemas ────────────────────────────────────────────────────
# These are passed as `tools` to every client.messages.create() call.

TOOLS = [
    {
        "name": "safety_check",
        "description": (
            "Step 1 — Run safety checks on the ticket. "
            "Detects prompt injection, credential exposure, hostile language, "
            "active security incidents, and automated system alerts. "
            "Must be called FIRST before any other tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key":         {"type": "string", "description": "Jira ticket key, e.g. KAN-12"},
                "summary":     {"type": "string", "description": "Ticket summary / title"},
                "description": {"type": "string", "description": "Full ticket description body"},
            },
            "required": ["key", "summary", "description"],
        },
    },
    {
        "name": "triage",
        "description": (
            "Step 2 — Classify the ticket before policy lookup. "
            "Checks scope, language, ambiguity, multi-part requests, "
            "speculative questions, and authorization issues. "
            "Only call this after safety_check returns safe=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key":         {"type": "string"},
                "summary":     {"type": "string"},
                "description": {"type": "string"},
                "issue_type":  {"type": "string", "description": "Jira issue type, e.g. Service Request"},
                "priority":    {"type": "string", "description": "Jira priority, e.g. High"},
            },
            "required": ["key", "summary", "description", "issue_type", "priority"],
        },
    },
    {
        "name": "policy_lookup",
        "description": (
            "Step 3 — Retrieve relevant policy chunks from ChromaDB and generate "
            "a grounded answer using Claude. Only call this after triage passes. "
            "Returns RESOLVE with a cited answer, or DEFER with LOW_CONFIDENCE."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key":         {"type": "string"},
                "summary":     {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["key", "summary", "description"],
        },
    },
    {
        "name": "finalize_decision",
        "description": (
            "Final step — record the agent's decision. "
            "Call this exactly once after all skill steps are complete. "
            "Provide the full AgentDecision fields. "
            "IMPORTANT: For DEFER decisions, policy_citation MUST be null — never populate it. "
            "For RESOLVE decisions, answer and policy_citation are required. "
            "For DEFER decisions, reason_code and reason_detail are required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action":          {"type": "string", "enum": ["RESOLVE", "DEFER"]},
                "answer":          {"type": ["string", "null"], "description": "Grounded answer — RESOLVE only; null for DEFER"},
                "policy_citation": {"type": ["string", "null"], "description": "e.g. POL-01 §1.1 — RESOLVE only; MUST be null for DEFER"},
                "reason_code":     {"type": ["string", "null"], "description": "DEFER reason code; null for RESOLVE"},
                "reason_detail":   {"type": ["string", "null"], "description": "One-sentence explanation — DEFER only; null for RESOLVE"},
                "confidence":      {"type": "number",  "description": "0.0 to 1.0"},
            },
            "required": ["action", "confidence"],
        },
    },
]
