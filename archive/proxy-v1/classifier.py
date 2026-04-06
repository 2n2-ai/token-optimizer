"""Request complexity classifier.

Heuristic-based classification of Anthropic Messages API requests
into tiers: SIMPLE, MEDIUM, COMPLEX, REASONING.
"""

import re
from typing import Dict, List, Tuple

# Keyword sets (lowercased)
SIMPLE_KEYWORDS = {
    "hi", "hello", "hey", "thanks", "thank you", "yes", "no",
    "what is", "define", "list", "who is", "when was", "where is",
}
MEDIUM_KEYWORDS = {
    "summarize", "explain", "extract", "compare", "analyze",
    "translate", "rewrite", "describe", "outline", "convert",
}
COMPLEX_KEYWORDS = {
    "architect", "design", "refactor", "debug complex",
    "write a full", "implement", "build", "create a complete",
    "review this codebase", "multi-step", "comprehensive",
}
REASONING_KEYWORDS = {
    "prove", "derive", "step by step", "mathematical",
    "formal logic", "theorem", "proof", "induction",
    "equation", "calculate precisely",
}

CODE_BLOCK_RE = re.compile(r"```")
EQUATION_RE = re.compile(r"[=∑∫∂√±≈≠≤≥∀∃]|\\frac|\\sum|\\int|\$\$")


def _extract_text(messages: List[Dict]) -> str:
    """Flatten messages list into a single text string."""
    parts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
    return "\n".join(parts)


def _count_code_blocks(text: str) -> int:
    """Count fenced code blocks (pairs of ```)."""
    return len(CODE_BLOCK_RE.findall(text)) // 2


def _keyword_score(text: str, keywords: set) -> int:
    """Count how many keyword phrases appear in text."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw in lower)


def classify(request_body: Dict) -> Tuple[str, Dict]:
    """Classify a Messages API request body.

    Returns (tier, details) where tier is one of:
        SIMPLE, MEDIUM, COMPLEX, REASONING
    and details is a dict with classification metadata.
    """
    messages = request_body.get("messages", [])
    system = request_body.get("system", "")
    if isinstance(system, list):
        system = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in system
        )

    user_text = _extract_text(messages)
    full_text = system + "\n" + user_text
    text_len = len(full_text)
    code_blocks = _count_code_blocks(full_text)
    num_messages = len(messages)
    system_len = len(system)

    # Estimate system prompt tokens (~4 chars per token)
    system_tokens_est = system_len // 4

    scores = {
        "SIMPLE": _keyword_score(full_text, SIMPLE_KEYWORDS),
        "MEDIUM": _keyword_score(full_text, MEDIUM_KEYWORDS),
        "COMPLEX": _keyword_score(full_text, COMPLEX_KEYWORDS),
        "REASONING": _keyword_score(full_text, REASONING_KEYWORDS),
    }

    has_equations = bool(EQUATION_RE.search(full_text))

    details = {
        "text_length": text_len,
        "code_blocks": code_blocks,
        "num_messages": num_messages,
        "system_tokens_est": system_tokens_est,
        "keyword_scores": scores,
        "has_equations": has_equations,
    }

    # REASONING: equations/formal notation or strong reasoning keywords
    if has_equations and scores["REASONING"] >= 1:
        return "REASONING", details
    if scores["REASONING"] >= 2:
        return "REASONING", details

    # COMPLEX: long text, multiple code blocks, or complex keywords
    if text_len > 5000:
        return "COMPLEX", details
    if code_blocks >= 2:
        return "COMPLEX", details
    if scores["COMPLEX"] >= 1:
        return "COMPLEX", details
    if system_tokens_est > 2000:
        return "COMPLEX", details

    # MEDIUM: keyword-driven (check before SIMPLE so "summarize" doesn't
    # fall through to SIMPLE just because the text is short)
    if scores["MEDIUM"] >= 1:
        return "MEDIUM", details

    # SIMPLE: short, single-turn, no code
    if (text_len < 500 and code_blocks == 0 and num_messages <= 1
            and scores["SIMPLE"] >= 1):
        return "SIMPLE", details
    if text_len < 200 and code_blocks == 0 and num_messages <= 1:
        return "SIMPLE", details
    if text_len < 500 and code_blocks == 0:
        return "SIMPLE", details

    return "MEDIUM", details
