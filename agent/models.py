"""
All shared data models.
Keep this file as the single source of truth for types used across skills.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────

class AgentAction(str, Enum):
    RESOLVE = "RESOLVE"
    DEFER   = "DEFER"


class ReasonCode(str, Enum):
    # Routing / scope
    OUT_OF_SCOPE             = "OUT_OF_SCOPE"
    AMBIGUOUS                = "AMBIGUOUS"
    NON_ENGLISH              = "NON_ENGLISH"
    MULTI_PART_COMPLEX       = "MULTI_PART_COMPLEX"
    SPECULATIVE              = "SPECULATIVE"
    HALLUCINATED_POLICY      = "HALLUCINATED_POLICY"
    AUTOMATED_SYSTEM_ALERT   = "AUTOMATED_SYSTEM_ALERT"
    CLARIFICATION_NEEDED     = "CLARIFICATION_NEEDED"
    # Security
    PROMPT_INJECTION             = "PROMPT_INJECTION"
    SENSITIVE_CREDENTIAL_EXPOSURE = "SENSITIVE_CREDENTIAL_EXPOSURE"
    HOSTILE_LANGUAGE             = "HOSTILE_LANGUAGE"
    ACTIVE_INCIDENT              = "ACTIVE_INCIDENT"
    SOCIAL_ENGINEERING_SUSPECTED = "SOCIAL_ENGINEERING_SUSPECTED"
    APPROVAL_REQUIRED            = "APPROVAL_REQUIRED"
    AUTHORIZATION_UNVERIFIED     = "AUTHORIZATION_UNVERIFIED"
    POLICY_VIOLATION_SUSPECTED   = "POLICY_VIOLATION_SUSPECTED"
    # Confidence
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


# ── Core domain objects ────────────────────────────────────────────────────

class JiraTicket(BaseModel):
    key:        str
    summary:    str
    description: str = ""
    issue_type: str  = "Service Request"
    priority:   str  = "Medium"
    labels:     list[str] = Field(default_factory=list)
    status:     str  = "To Do"


class AgentDecision(BaseModel):
    action:          AgentAction
    answer:          Optional[str]        = None   # RESOLVE only
    policy_citation: Optional[str]        = None   # e.g. "POL-01 §1.1"
    reason_code:     Optional[ReasonCode] = None   # DEFER only
    reason_detail:   Optional[str]        = None
    confidence:      float                = 0.0
    chunks_used:     list[str]            = Field(default_factory=list)


class TicketResult(BaseModel):
    ticket_key:    str
    ticket_summary: str
    decision:      AgentDecision
    comment_posted: bool          = False
    label_applied:  Optional[str] = None
    error:          Optional[str] = None


# ── Skill result types (returned by each skill fn, fed to orchestrator) ───

class SafetyResult(BaseModel):
    safe:        bool
    reason_code: Optional[ReasonCode] = None
    detail:      Optional[str]        = None


class TriageResult(BaseModel):
    pass_triage: bool                  # True = proceed to policy lookup
    reason_code: Optional[ReasonCode] = None
    detail:      Optional[str]        = None
    language:    str                   = "en"
    is_multi_part: bool                = False


class PolicyResult(BaseModel):
    answerable:      bool
    answer:          Optional[str] = None
    policy_citation: Optional[str] = None
    confidence:      float         = 0.0
    chunks_used:     list[str]     = Field(default_factory=list)
