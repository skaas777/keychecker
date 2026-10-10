from enum import Enum


def redact(key_str: str) -> str:
    """Mask an API key for progress/log output: first 6 + last 4 chars.

    Used only for progress chatter — results and snapshots keep full keys,
    since recovering usable keys is the tool's purpose.
    """
    if not key_str:
        return key_str
    if len(key_str) <= 14:
        return key_str[:2] + "…" + key_str[-2:] if len(key_str) > 4 else "…"
    return key_str[:6] + "…" + key_str[-4:]


class Provider(Enum):
    OPENAI = 1
    ANTHROPIC = 2
    AI21 = 3
    MAKERSUITE = 4
    AWS = 5
    AZURE = 6
    VERTEXAI = 7
    MISTRAL = 8
    OPENROUTER = 9
    ELEVENLABS = 10
    DEEPSEEK = 11
    XAI = 12


# Default attribute values per provider — replaces the 12-branch if/elif
# ladder that used to live in APIKey.__init__.
_PROVIDER_DEFAULTS = {
    Provider.OPENAI: {
        "model": "",
        "has_quota": False,
        "default_org": "",
        "organizations": [],
        "rpm": 0,
        "tier": "",
        "has_special_models": False,
        "real_32k": False,
        "the_one": False,
        "extra_models": False,
        "extra_model_list": set(),
        "id_verified": False,
        "slop": True,
        "missing_models": set(),
        "access_to_model_listing": True,
    },
    Provider.ANTHROPIC: {
        "pozzed": False,
        "rate_limited": False,
        "has_quota": True,
        "tier": "",
        "remaining_tokens": 0,
    },
    Provider.AI21: {
        "trial_elapsed": False,
    },
    Provider.AWS: {
        "username": "",
        "useless": True,
        "admin_priv": False,
        "bedrock_enabled": False,
        "region": "",
        "alt_regions": [],
        "useless_reasons": [],
        "logged": False,
        "models": {},
    },
    Provider.AZURE: {
        "endpoint": "",
        "best_deployment": "",
        "model": "",
        "deployments": [],
        "unfiltered": False,
        "dalle_deployments": "",
        "has_gpt4_turbo": [],
    },
    Provider.VERTEXAI: {
        "project_id": "",
        "has_opus": False,
    },
    Provider.MISTRAL: {
        "subbed": False,
    },
    Provider.MAKERSUITE: {
        "models": [],
        "enabled_billing": False,
        "tier": "",
    },
    Provider.OPENROUTER: {
        "usage": 0,
        "credit_limit": 0,
        "rpm": 0,
        "balance": 0,
        "limit_reached": False,
        "bought_credits": False,
    },
    Provider.ELEVENLABS: {
        "characters_left": 0,
        "usage": "",
        "tier": "",
        "unlimited": False,
        "pro_voice_limit": 0,
    },
    Provider.DEEPSEEK: {
        "balance": "$0.0 USD",
        "available": False,
        "rate_limited": False,
    },
    Provider.XAI: {
        "blocked": True,
        "subbed": False,
    },
}

# Fields emitted by to_dict() per provider, in the original order —
# replaces the second 12-branch ladder.
_PROVIDER_FIELDS = {
    Provider.OPENAI: [
        "model", "has_quota", "default_org", "organizations", "rpm", "tier",
        "has_special_models", "real_32k", "the_one", "extra_models",
        "extra_model_list", "id_verified", "slop", "missing_models",
        "access_to_model_listing",
    ],
    Provider.ANTHROPIC: ["pozzed", "rate_limited", "has_quota", "tier", "remaining_tokens"],
    Provider.AI21: ["trial_elapsed"],
    Provider.AWS: [
        "username", "useless", "admin_priv", "bedrock_enabled", "region",
        "alt_regions", "useless_reasons", "logged", "models",
    ],
    Provider.AZURE: [
        "endpoint", "best_deployment", "model", "deployments", "unfiltered",
        "dalle_deployments", "has_gpt4_turbo",
    ],
    Provider.VERTEXAI: ["project_id", "has_opus"],
    Provider.MISTRAL: ["subbed"],
    Provider.MAKERSUITE: ["models", "enabled_billing", "tier"],
    Provider.OPENROUTER: ["usage", "credit_limit", "rpm", "balance", "limit_reached", "bought_credits"],
    Provider.ELEVENLABS: ["characters_left", "usage", "tier", "unlimited", "pro_voice_limit"],
    Provider.DEEPSEEK: ["balance", "available", "rate_limited"],
    Provider.XAI: ["blocked", "subbed"],
}


class APIKey:
    def __init__(self, provider, api_key):
        self.provider = provider
        self.api_key = api_key
        for attr, default in _PROVIDER_DEFAULTS[provider].items():
            setattr(self, attr, default)

    def clone(self):
        cloned_key = APIKey(self.provider, self.api_key)
        cloned_key.__dict__ = self.__dict__.copy()
        return cloned_key

    def to_dict(self):
        """Return structured JSON-serializable dict for web rendering."""
        base = {"provider": self.provider.name, "api_key": self.api_key}
        for field in _PROVIDER_FIELDS[self.provider]:
            value = getattr(self, field)
            if isinstance(value, set):
                value = sorted(value)
            base[field] = value
        return base
