"""Model factory.

`chat_model(model, role)` builds a LangChain chat model. The provider is inferred
from the model name (gemini-* → Google, gpt-*/o[1-4]* → OpenAI, claude-* →
Anthropic); the role (`generator` / `coder` / `critic` / `formatter`) sets
temperature and, for Gemini-3+, the thinking level. A `None` model falls back to
`_DEFAULT_MODEL`. Per-run overrides arrive via `PlanContext.*_model`.

The no-arg `proposer()/critic()/formatter()` wrappers honour the env overrides
`PROPOSE_MODEL / CRITIQUE_MODEL / FORMAT_MODEL` for the standalone examples.
"""

import logging
import os
from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

# The google-genai SDK warns "Both GOOGLE_API_KEY and GEMINI_API_KEY are set..."
# on every client construction; we standardize on GOOGLE_API_KEY and pass it
# explicitly, so silence just that logger.
logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)


_DEFAULT_MODEL = "gemini-3.1-flash-lite"

# temperature (+ Gemini thinking_level) per role.
_ROLE_DEFAULTS = {
    "generator": {"temperature": 1.0, "thinking_level": "low"},
    # Separate from `generator` so the engineer can stay low-temperature (correctness, not
    # creativity) and high-thinking while the planner/researcher stay creative.
    "coder":     {"temperature": 0.2, "thinking_level": "high"},
    "critic":    {"temperature": 0.4, "thinking_level": "low"},
    "formatter": {"temperature": 0.0},
}


def _google_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    return key


def _provider(model: str) -> str:
    """Infer the LangChain provider from a model name."""
    m = model.lower()
    if m.startswith(("gpt-", "gpt4", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    return "google"  # gemini-* and the default


def _is_openai_reasoning(model: str) -> bool:
    """o-series reasoning models (o1/o3/o4) reject the `temperature` arg."""
    return model.lower().startswith(("o1", "o3", "o4"))


def _gemini_supports_thinking_level(model: str) -> bool:
    """`thinking_level` is a Gemini-3+ knob; 2.x rejects it."""
    return model.lower().startswith("gemini-3")


@lru_cache(maxsize=None)
def chat_model(model: str | None = None, role: str = "generator"):
    """Build a chat model for `role`, honoring a per-run `model` override.

    Cached by `(model, role)` so repeated calls reuse the client. An unknown
    role falls back to the `formatter` settings.
    """
    model = model or _DEFAULT_MODEL
    cfg = _ROLE_DEFAULTS.get(role, _ROLE_DEFAULTS["formatter"])
    temperature = cfg.get("temperature")
    thinking_level = cfg.get("thinking_level")
    provider = _provider(model)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        kw = {"model": model}
        if temperature is not None and not _is_openai_reasoning(model):
            kw["temperature"] = temperature
        return ChatOpenAI(**kw)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kw = {"model": model}
        if temperature is not None:
            kw["temperature"] = temperature
        return ChatAnthropic(**kw)

    # google (gemini-* and the default)
    kw = {"model": model, "google_api_key": _google_api_key()}
    if temperature is not None:
        kw["temperature"] = temperature
    if thinking_level is not None and _gemini_supports_thinking_level(model):
        kw["thinking_level"] = thinking_level
    return ChatGoogleGenerativeAI(**kw)


# ── default (no-arg) wrappers — env overrides for the standalone examples ──


def proposer():
    return chat_model(os.environ.get("PROPOSE_MODEL"), "generator")


def critic():
    return chat_model(os.environ.get("CRITIQUE_MODEL"), "critic")


def formatter():
    return chat_model(os.environ.get("FORMAT_MODEL"), "formatter")
