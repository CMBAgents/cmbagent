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

Request fields for thinking models (each a dict of extra request fields, sent as the
request's `extra_body`; when two are combined, nested dicts such as `chat_template_kwargs`
are merged key by key, the later one winning):

- `no_thinking`: the fields that turn the model's thinking off, e.g.
  {"chat_template_kwargs": {"enable_thinking": false}}. Sent only by the two retries: an
  empty plain answer is asked once more with them, and an unusable structured answer is
  retried once with them (below).
- `thinking`: role -> fields merged into every request of that role, e.g.
  {"formatter": {"chat_template_kwargs": {"enable_thinking": false}}}. A role the
  registry does not name gets none; a role `chat_model` does not know is read under the
  same fallback key as its temperature ("formatter").
- `structured_body`: fields added to every `with_structured_output` call whatever the
  role, after the role's, e.g. a llama.cpp reasoning budget
  {"reasoning_budget_tokens": 4000, "reasoning_budget_message": "..."}.
- `max_tokens_retry`: `max_tokens` of the structured retry. Default: `max_tokens` when
  `no_thinking` is given (thinking off needs no larger limit), else 1.5x `max_tokens`.

A structured answer is retried once, and only once, when it is unusable: it stopped at the
output limit (openai.LengthFinishReasonError) or did not parse or validate (a ValueError:
pydantic ValidationError, OutputParserException, JSONDecodeError, no parsed answer, or the
schema's own `structured_answer_problem` check, e.g. a failing verdict that gives no
reason). Entries are read when a (model, role) pair is first built, and the effective entry
is logged then (never the key): registry edits reach a running process only after a
restart.

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


_BREVITY = ("Your previous answer to this request could not be used: it ran out of output tokens or did not match "
            "the required format. Keep any reasoning short and give the structured answer directly, with every "
            "field filled in.")


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


def _structured_error_types():
    """Errors that earn a structured call its one retry: an overflow (LengthFinishReasonError, a thinking model spent
    its budget reasoning) or an answer that does not parse or validate (pydantic ValidationError,
    OutputParserException, JSONDecodeError, LangChain's "no 'parsed' field", `structured_answer_problem`: all
    ValueError). An HTTP error from the server (openai.APIStatusError) is not one of them."""
    try:
        from openai import LengthFinishReasonError
        return (LengthFinishReasonError, ValueError)
    except ImportError:  # pragma: no cover - openai always ships with langchain_openai
        return (ValueError,)


_length_error_types = _structured_error_types   # earlier name


def _short(err, n=300) -> str:
    return ("%s: %s" % (type(err).__name__, " ".join(str(err).split())))[:n]


def _with_length_retry(first, second, label="structured call", note=""):
    """`first`, falling back once to `second` when `first` raises one of `_structured_error_types`. Both failures are
    logged; when the retry fails too, its own error is raised (the first one is its __context__)."""
    from langchain_core.runnables import RunnableLambda
    types = _structured_error_types()

    def _run(inp, config=None):
        try:
            return first.invoke(inp, config)
        except types as err:
            print("[llms] %s: structured answer unusable (%s); retrying once%s" % (label, _short(err), note),
                  file=sys.stderr, flush=True)
        try:
            return second.invoke(inp, config)
        except Exception as err2:
            print("[llms] %s: the structured retry failed too (%s)" % (label, _short(err2)), file=sys.stderr, flush=True)
            raise

    wrapper = RunnableLambda(_run, name="structured_retry")
    wrapper.first_attempt, wrapper.retry = first, second    # for introspection (tests)
    return wrapper


_with_structured_retry = _with_length_retry


def _merge_body(*bodies) -> dict | None:
    """Combine extra_body dicts, later ones winning; nested dicts (`chat_template_kwargs`) are merged key by key
    rather than replaced whole."""
    out: dict = {}
    for b in bodies:
        for k, v in (b or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _merge_body(out[k], v)
            else:
                out[k] = dict(v) if isinstance(v, dict) else v
    return out or None


def _is_structured_call(kwargs) -> bool:
    """Was this request bound by `with_structured_output`? json_schema / json_mode bind `response_format`, and every
    method binds `ls_structured_output_format` (function_calling binds the schema as a forced tool). A bare
    `bind_tools` (the ReAct researcher) is not structured: its turns keep the empty-answer retry."""
    return "response_format" in kwargs or "ls_structured_output_format" in kwargs


def _has_tool_calls(msg) -> bool:
    return bool(getattr(msg, "tool_calls", None) or getattr(msg, "invalid_tool_calls", None))


def _check_answer(out, schema):
    """Raise ValueError for a structured answer that parsed but cannot be used: none at all (function_calling with no
    tool call), or one the schema's `structured_answer_problem` rejects. Applied on OpenAI-compatible endpoints only,
    so other providers' answers are read exactly as before."""
    if isinstance(schema, type) and out is None:
        raise ValueError("the model gave no structured answer")
    problem = getattr(out, "structured_answer_problem", None)
    if callable(problem):
        reason = problem()
        if reason:
            raise ValueError("%s answer rejected: %s" % (type(out).__name__, reason))
    return out


_LOGGED_ENTRIES: set = set()


def _log_entry(name: str, e: dict, role, extra_body, max_tokens) -> None:
    """Once per (model, role): the registry fields this client was built with (never the key), so a run's log says
    which configuration produced its answers."""
    if (name, role) in _LOGGED_ENTRIES:
        return
    _LOGGED_ENTRIES.add((name, role))
    print("[llms] %s (role %s): max_tokens=%s max_tokens_retry=%s structured_output=%s no_thinking=%s "
          "thinking[%s]=%s structured_body=%s" % (
              name, role, max_tokens, e.get("max_tokens_retry"), e.get("structured_output", "json_schema"),
              json.dumps(e.get("no_thinking")), role, json.dumps(extra_body),
              json.dumps(e.get("structured_body"))), file=sys.stderr, flush=True)


def _compat_model(name: str, e: dict, temperature, role: str | None = None):
    from langchain_openai import ChatOpenAI
    from langchain_core.runnables import RunnableLambda

    method = e.get("structured_output", "json_schema")

    class _CompatChatOpenAI(ChatOpenAI):
        """ChatOpenAI pointed at an OpenAI-compatible server, with the server's structured-output method.

        A structured call sends the entry's `structured_body` (e.g. a reasoning budget) on top of the role's fields. An
        unusable structured answer (see `_structured_error_types`: an overflow, or an answer that does not parse or
        validate) is retried once, with the endpoint's `no_thinking` fields and the same `max_tokens` when the registry
        gives them (else 1.5x `max_tokens`, or `max_tokens_retry`), and a note asking for brief reasoning; a second
        failure raises. Without this, one overlong evaluator or formatter answer ended the whole run: DeepSeek and Qwen
        step evaluators overflowed 32k and 48k tokens and ended 9 of 12 overnight benchmark runs (22-23 Nov 2026)."""

        def invoke(self, input, config=None, **kwargs):
            """A plain answer with no text (a thinking model spent its whole budget reasoning: finish_reason
            "length", content empty) is asked once more, with the endpoint's `no_thinking` request fields when the
            registry gives them and a note asking for a direct answer. The engineer's six attempts were otherwise
            all lost to the same overlong thinking, at up to five minutes each. A reply that holds tool calls is not
            empty, and a structured call is left to the structured retry (one retry path, not two nested)."""
            msg = super().invoke(input, config=config, **kwargs)
            if _msg_text(msg).strip() or _has_tool_calls(msg) or _is_structured_call(kwargs):
                return msg
            reason = (getattr(msg, "response_metadata", None) or {}).get("finish_reason")
            extra = e.get("no_thinking")
            print("[llms] %s returned an empty answer (finish_reason=%s); asking once more%s" % (
                self.model_name, reason, " with thinking off" if extra else ""), file=sys.stderr, flush=True)
            retry = self.model_copy(update={"extra_body": _merge_body(self.extra_body, extra)}) if extra else self
            return ChatOpenAI.invoke(retry, _add_brevity(input, _EMPTY_NOTE), config=config, **kwargs)

        def with_structured_output(self, schema=None, *, method=None, **kwargs):
            base = self
            if e.get("structured_body"):
                base = self.model_copy(update={"extra_body": _merge_body(self.extra_body, e["structured_body"])})
            check = RunnableLambda(lambda out: _check_answer(out, schema), name="check_structured_answer")
            first = ChatOpenAI.with_structured_output(base, schema, method=method or _method, **kwargs) | check
            no_thinking = e.get("no_thinking")
            if not (self.max_tokens or no_thinking):
                return first
            if no_thinking:   # thinking off: the same limit is ample
                limit = e.get("max_tokens_retry") or self.max_tokens
                update = {"extra_body": _merge_body(base.extra_body, no_thinking),
                          "max_tokens": int(limit) if limit else None}
            else:
                update = {"max_tokens": int(e.get("max_tokens_retry") or int(1.5 * self.max_tokens))}
            retry_model = self.model_copy(update=update)
            second = RunnableLambda(_add_brevity) | ChatOpenAI.with_structured_output(
                retry_model, schema, method=method or _method, **kwargs) | check
            label = "%s %s" % (self.model_name, getattr(schema, "__name__", "structured call"))
            note = (" with thinking off (max_tokens=%s)" % update["max_tokens"] if no_thinking
                    else " with max_tokens=%s" % update["max_tokens"])
            return _with_length_retry(first, second, label=label, note=note)

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
    role_body = _merge_body((e.get("thinking") or {}).get(role)) if role else None
    if role_body:
        kw["extra_body"] = role_body
    _log_entry(name, e, role, role_body, kw.get("max_tokens"))
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


def _role_key(role: str) -> str:
    """The `_ROLE_DEFAULTS` key a role is read under; an unknown role falls back to the formatter's (temperature and
    the registry's `thinking` fields alike)."""
    return role if role in _ROLE_DEFAULTS else "formatter"


@lru_cache(maxsize=None)
def chat_model(model: str | None = None, role: str = "generator"):
    """Build a chat model for `role`, honoring a per-run `model` override.

    Cached by `(model, role)` so repeated calls reuse the client. An unknown
    role falls back to the `formatter` settings.
    """
    model = model or _DEFAULT_MODEL
    role_key = _role_key(role)
    cfg = _ROLE_DEFAULTS[role_key]
    temperature = cfg.get("temperature")
    thinking_level = cfg.get("thinking_level")
    provider = _provider(model)

    if provider == "compat":
        return _compat_model(model, endpoints()[model], temperature, role_key)

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
