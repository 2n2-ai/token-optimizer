"""Model router.

Selects the optimal Anthropic model based on classified tier.
"""

from proxy.config import MODELS


def route(tier: str) -> dict:
    """Return routing decision for a given tier.

    Returns dict with:
        model: the model ID to use
        tier: the tier label
        extended_thinking: whether to enable extended thinking
    """
    model = MODELS.get(tier, MODELS["MEDIUM"])
    extended_thinking = (tier == "REASONING")

    return {
        "model": model,
        "tier": tier,
        "extended_thinking": extended_thinking,
    }
