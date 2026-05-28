"""
Policy RAG Skill — Step 3 of the pipeline.

1. Retrieves top-k policy chunks from ChromaDB
2. Confidence gate — below threshold → DEFER without calling LLM
3. Calls shared LLM client with a tight grounding prompt
4. Returns PolicyResult with answer + citation, or not-answerable
"""
import json

import chromadb
import structlog
from chromadb.utils import embedding_functions

from agent.config import settings
from agent.llm_client import call_llm, extract_json
from agent.models import PolicyResult
from agent.prompts import policy_rag as rag_prompts

log = structlog.get_logger(__name__)


# ── ChromaDB singleton ────────────────────────────────────────────────────

_collection = None


def _get_collection():
    global _collection
    if _collection is not None:
        return _collection
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    ef = embedding_functions.DefaultEmbeddingFunction()
    _collection = client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    log.info("chroma_ready", chunks=_collection.count())
    return _collection


# ── Retrieval ─────────────────────────────────────────────────────────────

def _retrieve(query: str) -> list[dict]:
    """Return top-k chunks with similarity scores."""
    col = _get_collection()
    if col.count() == 0:
        log.warning("chroma_empty", hint="Run: poetry run python scripts/ingest_policies.py")
        return []

    k = min(settings.max_chunks, col.count())
    results = col.query(
        query_texts=[query],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "document": doc,
            "metadata": meta,
            "similarity": round(1 - dist, 4),
        })
    return chunks


def _format_chunks(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        m = c["metadata"]
        lines.append(
            f"[{i}] {m.get('policy_id','?')} {m.get('section','?')} "
            f"(similarity={c['similarity']:.3f})\n{c['document']}"
        )
    return "\n\n".join(lines)


# ── LLM grounded answer ───────────────────────────────────────────────────

def _ask_llm(key: str, summary: str, description: str, chunks: list[dict]) -> PolicyResult:
    top_sim = chunks[0]["similarity"] if chunks else 0.0

    user_msg = rag_prompts.USER_TEMPLATE.format(
        key=key,
        summary=summary,
        description=description or "(empty)",
        n_chunks=len(chunks),
        chunks_text=_format_chunks(chunks),
        top_sim=top_sim,
        threshold=settings.confidence_threshold,
    )

    raw = call_llm(system=rag_prompts.SYSTEM, user=user_msg, max_tokens=512)

    try:
        data = json.loads(extract_json(raw))
        return PolicyResult(
            answerable=bool(data.get("answerable", False)),
            answer=data.get("answer"),
            policy_citation=data.get("policy_citation"),
            confidence=float(data.get("confidence", 0.0)),
            chunks_used=[c["document"] for c in chunks],
        )
    except Exception:
        log.warning("policy_rag_parse_error", key=key, raw=raw[:120])
        return PolicyResult(
            answerable=False,
            confidence=0.0,
            chunks_used=[c["document"] for c in chunks],
        )


# ── Public entry point ────────────────────────────────────────────────────

def run(key: str, summary: str, description: str) -> PolicyResult:
    """
    Retrieve relevant policy chunks and generate a grounded answer.
    Returns PolicyResult(answerable=True) with answer + citation on success.
    """
    query = f"{summary} {description}"
    chunks = _retrieve(query)

    top_sim = chunks[0]["similarity"] if chunks else 0.0
    if top_sim < settings.confidence_threshold:
        log.info("policy_rag_low_confidence", key=key, top_sim=top_sim)
        return PolicyResult(
            answerable=False,
            confidence=top_sim,
            chunks_used=[c["document"] for c in chunks],
        )

    result = _ask_llm(key, summary, description, chunks)

    if result.answerable:
        log.info("policy_rag_resolved", key=key,
                 citation=result.policy_citation, confidence=result.confidence)
    else:
        log.info("policy_rag_not_answerable", key=key, confidence=result.confidence)

    return result
