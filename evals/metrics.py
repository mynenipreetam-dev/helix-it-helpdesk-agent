"""
Custom DeepEval metrics for the Helix IT Helpdesk Agent.

Metrics:
  1. ActionAccuracyMetric  — did the agent RESOLVE vs DEFER correctly?
  2. CitationGroundednessMetric — is the citation present in retrieved chunks?
  3. ReasonCodeAccuracyMetric  — did the agent pick the right DEFER reason code?
"""
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


class ActionAccuracyMetric(BaseMetric):
    """
    Pass if agent action matches ground truth (RESOLVE or DEFER).
    This is the primary correctness signal.
    """
    name = "Action Accuracy"
    threshold = 1.0

    def measure(self, test_case: LLMTestCase) -> float:
        expected = (test_case.expected_output or "").strip().upper()
        actual   = (test_case.actual_output   or "").strip().upper()
        self.score = 1.0 if expected == actual else 0.0
        self.success = self.score >= self.threshold
        self.reason = (
            f"Expected={expected}, Got={actual}"
            if not self.success
            else "Action matches ground truth"
        )
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success


class CitationGroundednessMetric(BaseMetric):
    """
    For RESOLVE tickets: pass if the agent's policy_citation is grounded
    (the cited policy ID appears in retrieval_context) AND if the citation
    starts with the expected primary section OR an accepted extra section.

    Partial / startswith matching is intentional — the agent may legitimately
    cite multiple sections (e.g. "POL-02 §2.4, POL-02 §2.5") and that is
    considered correct as long as the primary expected section is covered.

    Skipped (score=1.0) for DEFER tickets.
    """
    name = "Citation Groundedness"
    threshold = 1.0

    @staticmethod
    def _normalise(s: str) -> str:
        """Lower-case, strip whitespace, collapse multiple spaces."""
        return " ".join(s.lower().split())

    def measure(self, test_case: LLMTestCase) -> float:
        expected_action = (test_case.expected_output or "").strip().upper()

        # Only applies to RESOLVE tickets
        if expected_action != "RESOLVE":
            self.score = 1.0
            self.success = True
            self.reason = "N/A — DEFER ticket, citation check skipped"
            return self.score

        # additional_metadata carries the agent's policy_citation
        meta     = test_case.additional_metadata or {}
        citation = (meta.get("policy_citation") or "").strip()
        context  = " ".join(test_case.retrieval_context or [])

        if not citation:
            # DEFER decision when action was expected to RESOLVE
            self.score = 0.0
            self.success = False
            self.reason = "No citation provided by agent"
            return self.score

        # ── Groundedness check ───────────────────────────────────────────────
        # The agent citation may be multi-section, e.g. "POL-02 §2.4, POL-02 §2.5"
        # Pull the first policy ID from the citation string.
        first_pol_id = citation.split()[0]  # e.g. "POL-02"
        grounded = first_pol_id.upper() in context.upper()

        if not grounded:
            self.score = 0.0
            self.success = False
            self.reason = f"Citation '{citation}' NOT found in retrieved chunks — possible hallucination"
            return self.score

        # ── Correctness check (partial / startswith match) ───────────────────
        # Collect the primary expected citation + any accepted extras.
        expected_primary = self._normalise(meta.get("expected_citation") or "")
        extra_citations  = meta.get("extra_citations") or []
        all_accepted     = [expected_primary] + [self._normalise(e) for e in extra_citations]

        agent_norm = self._normalise(citation)

        # Pass if the agent's full citation string STARTS WITH or CONTAINS any
        # accepted section (handles multi-section answers gracefully).
        citation_correct = any(
            agent_norm.startswith(acc) or acc in agent_norm
            for acc in all_accepted
            if acc  # skip empty strings
        )

        self.score = 1.0 if citation_correct else 0.0
        self.success = self.score >= self.threshold
        self.reason = (
            f"Citation '{citation}' is grounded and matches accepted sections"
            if citation_correct
            else (
                f"Citation '{citation}' is grounded but does not match expected "
                f"'{expected_primary}' (or extras: {extra_citations})"
            )
        )
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success


class ReasonCodeAccuracyMetric(BaseMetric):
    """
    For DEFER tickets: pass if the agent's reason code matches ground truth.
    Skipped (score=1.0) for RESOLVE tickets.
    """
    name = "Reason Code Accuracy"
    threshold = 1.0

    def measure(self, test_case: LLMTestCase) -> float:
        expected_action = (test_case.expected_output or "").strip().upper()

        if expected_action != "DEFER":
            self.score = 1.0
            self.success = True
            self.reason = "N/A — RESOLVE ticket, reason code check skipped"
            return self.score

        expected_code = (test_case.additional_metadata or {}).get("expected_reason_code", "")
        actual_code   = (test_case.additional_metadata or {}).get("actual_reason_code", "")

        match = expected_code.upper() == actual_code.upper() if expected_code else False
        self.score = 1.0 if match else 0.0
        self.success = self.score >= self.threshold
        self.reason = (
            f"Reason code matches: {actual_code}"
            if match
            else f"Expected reason code '{expected_code}', got '{actual_code}'"
        )
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success
