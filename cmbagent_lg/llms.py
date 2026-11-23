"""Model factory.

`chat_model(model, role)` builds a LangChain chat model. A model listed in the endpoint
registry (below) is served by an OpenAI-compatible endpoint; otherwise the provider is
inferred from the model name (gemini-* → Google, gpt-*/o[1-4]* → OpenAI, claude-* →
Anthropic). The role (`generator` / `coder` / `critic` / `formatter`) sets temperature and,
for Gemini-3+, the thinking level. A `None` model falls back to `_DEFAULT_MODEL`. Per-run
overrides arrive via `PlanContext.*_model`.

Endpoint registry: `LLM_ENDPOINTS` names a JSON file that maps model names to servers that
speak the OpenAI chat-completions API (vLLM, llama.cpp, SGLang, hosted gateways), e.g.

    {"qwen3.8-flash-next": {"base_url": "http://127.0.0.1:30000/v1",
                            "key_file": "~/.config/llm/qwen.key"},
     "kimi-k3": {"base_url": "https://llm.example.org/v1", "model": "moonshotai/Kimi-K3",
                 "key_file": "~/.config/llm/endpoint.env", "key_var": "API_KEY",
                 "max_tokens": 32000, "structured_output": "json_schema"}}

Per entry: `base_url` (required); `model`, the id the server expects (default: the
registry name); the key from `api_key_env` (an environment variable), or `key_file` (a bare
key, or NAME=value lines with `key_var` naming the line), or "EMPTY"; optional
`max_tokens`, `timeout` (s), `temperature` (overrides the role's), and
`structured_output` ("json_schema", the default, or "function_calling") for servers that
reject JSON-schema response formats. Keys are read from files or the environment and
never logged.

The no-arg `proposer()/critic()/formatter()` wrappers honour the env overrides
`PROPOSE_MODEL / CRITIQUE_MODEL / FORMAT_MODEL` for the standalone examples.
"""

import json
import logging
import os
import sys
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


@lru_cache(maxsize=8)
def _registry(path: str, mtime: float) -> dict:
    with open(path) as fh:
        reg = json.load(fh)
    if not isinstance(reg, dict):
        raise ValueError("%s: the endpoint registry must be a JSON object of model -> endpoint" % path)
    for name, e in reg.items():
        if not isinstance(e, dict) or not e.get("base_url"):
            raise ValueError("%s: entry %r needs a base_url" % (path, name))
    return reg


def endpoints() -> dict:
    """The endpoint registry named by LLM_ENDPOINTS ({} when unset)."""
    path = os.environ.get("LLM_ENDPOINTS")
    if not path:
        return {}
    path = os.path.expanduser(path)
    return _registry(path, os.path.getmtime(path))


def endpoint_api_key(e: dict) -> str:
    """The API key of a registry entry: from `api_key_env`, `key_file` (+ `key_var`), else \"EMPTY\"."""
    if e.get("api_key_env"):
        key = os.environ.get(e["api_key_env"])
        if not key:
            raise RuntimeError("environment variable %s (api key for %s) is not set" % (e["api_key_env"], e["base_url"]))
        return key
    if e.get("key_file"):
        lines = [ln.strip() for ln in open(os.path.expanduser(e["key_file"])) if ln.strip() and not ln.startswith("#")]
        pairs = [ln.split("=", 1) for ln in lines if "=" in ln]
        if not pairs:
            return lines[0]
        if e.get("key_var"):
            for k, v in pairs:
                if k.strip() == e["key_var"]:
                    return v.strip().strip("'\"")
            raise RuntimeError("%s has no %s line" % (e["key_file"], e["key_var"]))
        if len(pairs) == 1:
            return pairs[0][1].strip().strip("'\"")
        raise RuntimeError("%s has several NAME=value lines: set key_var" % e["key_file"])
    return "EMPTY"


_BREVITY = ("Your previous answer to this request ran out of output tokens. Keep any reasoning short and give the "
            "structured answer directly.")


_EMPTY_NOTE = ("Your previous answer to this request was empty: the output-token budget ran out before the answer "
               "began. Keep any reasoning short and write the answer directly.")


def _add_brevity(inp, note=_BREVITY):
    """Append a note (by default the brevity request) to a chat input (a string, a list of messages, or a prompt
    value)."""
    from langchain_core.messages import HumanMessage
    if isinstance(inp, str):
        return inp + "\n\n" + note
    msgs = inp.to_messages() if hasattr(inp, "to_messages") else list(inp)
    return msgs + [HumanMessage(note)]


def _msg_text(msg) -> str:
    t = getattr(msg, "text", "")
    if isinstance(t, str):                 # langchain-core 1.x: a str property (also callable, deprecated)
        return str(t)
    t = t() if callable(t) else t          # older versions: a method
    return t if isinstance(t, str) else str(getattr(msg, "content", "") or "")


def _length_error_types():
    try:
        from openai import LengthFinishReasonError
        return (LengthFinishReasonError,)
    except ImportError:  # pragma: no cover - openai always ships with langchain_openai
        return ()


def _with_length_retry(first, second):
    """`first`, falling back to `second` only when `first` stops at its output-token limit."""
    types = _length_error_types()
    return first.with_fallbacks([second], exceptions_to_handle=types) if types else first


def _compat_model(name: str, e: dict, temperature):
    from langchain_openai import ChatOpenAI

    method = e.get("structured_output", "json_schema")

    class _CompatChatOpenAI(ChatOpenAI):
        """ChatOpenAI pointed at an OpenAI-compatible server, with the server's structured-output method.

        A structured answer that stops at the output limit (openai.LengthFinishReasonError: a thinking model spent its
        budget reasoning) is retried once with a larger budget (`max_tokens_retry` of the registry entry, else 1.5x
        `max_tokens`) and a note asking for brief reasoning; a second overflow raises as before. Without this, one
        overlong evaluator or formatter answer ended the whole run."""

        def invoke(self, input, config=None, **kwargs):
            """A plain answer with no text (a thinking model spent its whole budget reasoning: finish_reason
            "length", content empty) is asked once more, with the endpoint's `no_thinking` request fields when the
            registry gives them and a note asking for a direct answer. The engineer's six attempts were otherwise
            all lost to the same overlong thinking, at up to five minutes each."""
            msg = super().invoke(input, config=config, **kwargs)
            if _msg_text(msg).strip():
                return msg
            reason = (getattr(msg, "response_metadata", None) or {}).get("finish_reason")
            extra = e.get("no_thinking")
            print("[llms] %s returned an empty answer (finish_reason=%s); asking once more%s" % (
                self.model_name, reason, " with thinking off" if extra else ""), file=sys.stderr, flush=True)
            retry = self.model_copy(update={"extra_body": {**(self.extra_body or {}), **extra}}) if extra else self
            return ChatOpenAI.invoke(retry, _add_brevity(input, _EMPTY_NOTE), config=config, **kwargs)

        def with_structured_output(self, schema=None, *, method=None, **kwargs):
            first = super().with_structured_output(schema, method=method or _method, **kwargs)
            if not self.max_tokens:
                return first
            bigger = int(e.get("max_tokens_retry") or int(1.5 * self.max_tokens))
            retry_model = self.model_copy(update={"max_tokens": bigger})
            from langchain_core.runnables import RunnableLambda
            second = RunnableLambda(_add_brevity) | ChatOpenAI.with_structured_output(
                retry_model, schema, method=method or _method, **kwargs)
            return _with_length_retry(first, second)

    _method = method
    kw = {"model": e.get("model") or name, "base_url": e["base_url"], "api_key": endpoint_api_key(e),
          "stream_usage": True}
    t = e.get("temperature", temperature)
    if t is not None:
        kw["temperature"] = t
    if e.get("max_tokens"):
        kw["max_tokens"] = int(e["max_tokens"])
    if e.get("timeout"):
        kw["timeout"] = float(e["timeout"])
    return _CompatChatOpenAI(**kw)


def _provider(model: str) -> str:
    """The provider of a model: "compat" if the endpoint registry lists it, else inferred from the name."""
    if model in endpoints():
        return "compat"
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

    if provider == "compat":
        return _compat_model(model, endpoints()[model], temperature)

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


_endpoint_key = endpoint_api_key   # earlier private name
