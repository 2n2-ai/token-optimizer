"""Proxy configuration constants."""

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 9400

ANTHROPIC_API_URL = "https://api.anthropic.com"

# Assumed model the user is paying for (baseline for savings calculation).
DEFAULT_MAX_MODEL = "claude-sonnet-4-6"

# Model tier mapping
MODELS = {
    "SIMPLE": "claude-haiku-4-5",
    "MEDIUM": "claude-sonnet-4-6",
    "COMPLEX": "claude-opus-4-6",
    "REASONING": "claude-sonnet-4-6",  # + extended thinking
}

# Pricing per million tokens
PRICING = {
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
    },
}

# Headers the proxy reads/writes
HEADER_MAX_MODEL = "x-token-optimizer-max-model"
HEADER_FORCE_MODEL = "x-token-optimizer-force-model"
HEADER_RESP_MODEL = "x-token-optimizer-model"
HEADER_RESP_TIER = "x-token-optimizer-tier"
HEADER_RESP_SAVINGS = "x-token-optimizer-savings"
