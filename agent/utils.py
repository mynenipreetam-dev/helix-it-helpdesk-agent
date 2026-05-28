"""
Shared utilities for the Helix agent.
"""
import re


def extract_json(raw: str) -> str:
    """
    Robustly extract a JSON object from a raw LLM response string.

    Handles all common Claude output patterns:
      - Clean JSON only
      - Wrapped in ```json ... ``` fences
      - Trailing markdown/prose after the closing brace
      - Leading/trailing whitespace

    Returns the extracted JSON string ready for json.loads().
    """
    # Strip leading/trailing whitespace
    text = raw.strip()

    # Remove ```json ... ``` or ``` ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```.*$", "", text, flags=re.DOTALL)
    text = text.strip()

    # Extract the first complete JSON object using brace matching
    start = text.find("{")
    if start == -1:
        return text  # let json.loads raise a meaningful error

    depth = 0
    in_string = False
    escape_next = False
    end = start

    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    return text[start: end + 1]
