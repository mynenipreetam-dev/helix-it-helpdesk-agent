# Helix IT Helpdesk Agent

> Auto-resolves IT support tickets grounded in Helix Industries' 10 IT policies.
> Built for the FDE Agentic Take-Home Assignment.

---

## Architecture

```
                        ┌─────────────────────────────────┐
  Jira Webhook ──────►  │         FastAPI App              │
  /batch trigger ─────► │  /webhook  /batch  /health       │
                        └────────────┬────────────────────┘
                                     │ JiraTicket
                                     ▼
                        ┌─────────────────────────────────┐
                        │        Orchestrator              │
                        │   Claude tool_use agentic loop   │
                        │                                  │
                        │  ┌──────────┐                    │
                        │  │  Step 1  │ safety_check()     │
                        │  │  Safety  │ regex + Claude      │
                        │  └────┬─────┘                    │
                        │       │ safe?                    │
                        │  ┌────▼─────┐                    │
                        │  │  Step 2  │ triage()           │
                        │  │  Triage  │ scope/lang/ambig    │
                        │  └────┬─────┘                    │
                        │       │ pass?                    │
                        │  ┌────▼─────┐                    │
                        │  │  Step 3  │ policy_lookup()    │
                        │  │ PolicyRAG│ ChromaDB + Claude  │
                        │  └────┬─────┘                    │
                        │       │ AgentDecision             │
                        │  ┌────▼─────┐                    │
                        │  │  Final   │ finalize_decision() │
                        │  │  Action  │ jira_actions.apply()│
                        │  └──────────┘                    │
                        └─────────────────────────────────┘
                                     │
                         ┌───────────┴────────────┐
                         │                        │
                      RESOLVE                   DEFER
                         │                        │
                  ✅ Comment answer        🔁 Comment reason_code
                  Label: agent-resolved   Label: needs-human
                  Transition: Done        Status: To Do (unchanged)
```

### Agentic Loop (Claude tool_use)

Claude acts as the conductor. It receives the ticket + 4 tool schemas and calls them in order:

```
1. Orchestrator sends: ticket + tools list to Claude
2. Claude emits:       tool_use { name: "safety_check", input: {...} }
3. Orchestrator runs:  safety.run(...)  →  SafetyResult
4. Orchestrator feeds: tool_result back to Claude
5. Claude emits:       tool_use { name: "triage", ... }  (or finalize_decision if unsafe)
6. Repeat until:       Claude calls finalize_decision → loop ends
```

Each skill short-circuits to `finalize_decision` if its check fails — no wasted LLM calls.

---

## Project Structure

```
helix-agent/
├── pyproject.toml              # Poetry — all deps + scripts
├── .env.example                # Copy to .env and fill secrets
│
├── agent/
│   ├── config.py               # Pydantic settings (loaded from .env)
│   ├── models.py               # JiraTicket, AgentDecision, enums — shared types
│   ├── orchestrator.py         # Agentic loop — Claude tool_use conductor
│   │
│   ├── skills/
│   │   ├── safety.py           # Step 1 — regex + LLM safety checks
│   │   ├── triage.py           # Step 2 — scope, language, ambiguity
│   │   ├── policy_rag.py       # Step 3 — ChromaDB retrieval + grounded answer
│   │   └── jira_actions.py     # Jira reads/writes — fetch, comment, label, transition
│   │
│   └── prompts/
│       ├── orchestrator.py     # Orchestrator system prompt + Claude tool schemas
│       ├── safety.py           # Safety agent prompt
│       ├── triage.py           # Triage agent prompt
│       └── policy_rag.py       # Grounded answer prompt (answer ONLY from chunks)
│
├── app/
│   └── main.py                 # FastAPI: /webhook  /batch  /batch/status  /health
│
├── evals/
│   ├── ground_truth.py         # 50 EvalCase objects (all ticket keys + expected outcomes)
│   ├── metrics.py              # DeepEval: ActionAccuracy, CitationGroundedness, ReasonCodeAccuracy
│   └── runner.py               # Eval runner — runs all 50, outputs CSV + DeepEval report
│
├── scripts/
│   └── ingest_policies.py      # Chunk POL-01..POL-10 → ChromaDB (run once)
│
└── chroma_db/                  # Persisted vector store (gitignored)
```

---

## Quick Start

### 1. Install dependencies

```bash
poetry install   # runtime + dev deps (includes deepeval)
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, JIRA_BASE_URL, JIRA_USER_EMAIL, JIRA_API_TOKEN
```

### 3. Ingest policies into ChromaDB

```bash
poetry run ingest
# Output: "Upserted 60 chunks → ./chroma_db"
```

### 4. Start the FastAPI server

```bash
poetry run dev
# → http://localhost:8000
# → http://localhost:8000/docs  (Swagger UI)

# Production-style (no auto-reload):
# poetry run serve
```

### 5. Trigger batch eval (dry run — no Jira writes)

```bash
curl -X POST http://localhost:8000/batch \
  -H "Content-Type: application/json" \
  -d '{"label": "eval-set", "dry_run": true}'
# Returns: {"job_id": "...", "status": "queued"}

# Poll for results:
curl http://localhost:8000/batch/status/<job_id>
```

### 6. Run DeepEval eval harness

```bash
poetry run deepeval
# Runs all 50 tickets, outputs eval_report.csv + DeepEval metrics
```

### 7. Register Jira webhook (for live processing)

In Jira: Project Settings → Webhooks → Create:
- URL: `https://<your-host>/webhook`
- Events: `Issue created`, `Issue updated`

---

## Prompt Strategy

| Agent | Prompt focus | Max tokens |
|-------|-------------|------------|
| Safety | Pattern detection only — no policy knowledge | 256 |
| Triage | Classification only — 8 DEFER categories | 256 |
| Policy RAG | Grounded answer from chunks — explicit "no prior knowledge" rule | 512 |
| Orchestrator | Tool orchestration only — calls skills, never answers directly | 1024 |

**Grounding enforcement:**
- Policy RAG prompt explicitly forbids prior knowledge
- Confidence threshold (default 0.45) gates retrieval before any LLM call
- Citations are validated: if `POL-XX` not present in retrieval context → CitationGroundednessMetric fails

---

## Grounding & Hallucination Prevention

1. **Retrieval gate** — if top cosine similarity < 0.45, DEFER immediately (no Claude call)
2. **Prompt constraint** — "Answer ONLY from the policy chunks provided. Do NOT use prior knowledge."
3. **Citation check** — eval metric verifies citation appears in retrieved chunks
4. **Short-circuit** — Safety + Triage catch 12 of 25 DEFER cases without touching the RAG stack

---

## Evaluation Metrics (DeepEval)

| Metric | Measures |
|--------|----------|
| `ActionAccuracyMetric` | RESOLVE vs DEFER correct? |
| `CitationGroundednessMetric` | Citation found in retrieved chunks? |
| `ReasonCodeAccuracyMetric` | Correct DEFER reason code? |

Run: `poetry run deepeval`
Output: `eval_report.csv` + DeepEval HTML report

### Poetry scripts

| Command | Description |
|---------|-------------|
| `poetry run ingest` | Chunk policies → ChromaDB |
| `poetry run dev` | Start API with auto-reload |
| `poetry run serve` | Start API without reload |
| `poetry run deepeval` | Run the 50-ticket eval harness |
| `poetry run clean` | Remove `__pycache__` and tool caches |

---

## What I'd Harden for Production

1. **Replace in-memory job store** with Redis (batch job persistence across restarts)
2. **Webhook idempotency** — store processed ticket keys to prevent double-processing on retries
3. **Structured logging → Datadog/Grafana** — currently structlog to stdout
4. **Rate limiting** on `/webhook` and `/batch`
5. **Chroma → managed vector DB** (Pinecone/pgvector) for HA and multi-instance deployments
6. **Human-in-the-loop escalation** — DEFER tickets should auto-assign to a Jira queue with SLA tracking
7. **Policy versioning** — re-ingest + re-eval on every policy update; flag drift in citations
8. **Confidence calibration** — tune threshold per policy cluster based on eval results
