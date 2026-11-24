"""Registry request fields for thinking models (`thinking`, `structured_body`), the single structured retry, tool-call
replies, the verdict checks applied to open-model answers, and the once-per-(model, role) log of the effective entry.

No model is called: ChatOpenAI.invoke is monkeypatched, as in tests/test_length_retry.py.

    PYTHONPATH=. python -m pytest tests/test_open_model_structured.py
"""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from cmbagent_lg import llms
from cmbagent_lg.self_debug.schemas import ExecutionVerdict, StepVerdict

NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
BUDGET = {"reasoning_budget_tokens": 4000, "reasoning_budget_message": "\n\nTime to answer.\n"}


def _entry(**kw):
    e = {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 32000}
    e.update(kw)
    return e


class V(BaseModel):
    ok: bool


def _validation_error():
    try:
        V.model_validate({"ok": "not a bool"})
    except ValidationError as err:
        return err


def _patch(monkeypatch, replies):
    """ChatOpenAI.invoke returns `replies` in turn (an AIMessage, or an exception to raise); returns the call log."""
    from langchain_openai import ChatOpenAI
    calls = []

    def invoke(self, input, config=None, **kwargs):
        calls.append({"extra_body": self.extra_body, "max_tokens": self.max_tokens, "input": input,
                      "kwargs": kwargs})
        r = replies[len(calls) - 1]
        if isinstance(r, BaseException):
            raise r
        return r
    monkeypatch.setattr(ChatOpenAI, "invoke", invoke)
    return calls


def _parsed(obj):
    return AIMessage(obj.model_dump_json(), additional_kwargs={"parsed": obj},
                     response_metadata={"finish_reason": "stop"})


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Point LLM_ENDPOINTS at a registry file and give chat_model a fresh cache."""
    def write(reg):
        p = tmp_path / "llm_endpoints.json"
        p.write_text(json.dumps(reg))
        monkeypatch.setenv("LLM_ENDPOINTS", str(p))
        llms.chat_model.cache_clear()
        return p
    yield write
    llms.chat_model.cache_clear()


# ── a. registry fields ─────────────────────────────────────────────────────


def test_thinking_fields_reach_the_role(registry):
    registry({"qwen-test": _entry(no_thinking=NO_THINKING, thinking={"formatter": NO_THINKING})})
    assert llms.chat_model("qwen-test", "formatter").extra_body == NO_THINKING
    assert llms.chat_model("qwen-test", "critic").extra_body is None
    assert llms.chat_model("qwen-test", "coder").extra_body is None


def test_unknown_role_uses_the_formatter_key_for_thinking_and_temperature(registry):
    registry({"qwen-test": _entry(thinking={"formatter": NO_THINKING})})
    m = llms.chat_model("qwen-test", "some_new_role")
    assert m.extra_body == NO_THINKING
    assert m.temperature == llms._ROLE_DEFAULTS["formatter"]["temperature"]


def test_gemini_is_unchanged(registry, monkeypatch):
    registry({"qwen-test": _entry(thinking={"critic": BUDGET}, structured_body=BUDGET)})
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
    m = llms.chat_model("gemini-3.1-flash-lite", "critic")
    assert type(m).__name__ == "ChatGoogleGenerativeAI"
    assert m.thinking_level == "low"
    assert not getattr(m, "extra_body", None)


def test_merge_body_merges_chat_template_kwargs():
    a = {"chat_template_kwargs": {"enable_thinking": True, "x": 1}, "reasoning_budget_tokens": 1}
    b = {"chat_template_kwargs": {"enable_thinking": False}}
    merged = llms._merge_body(a, b)
    assert merged == {"chat_template_kwargs": {"enable_thinking": False, "x": 1}, "reasoning_budget_tokens": 1}
    assert a["chat_template_kwargs"]["enable_thinking"] is True        # inputs untouched
    assert llms._merge_body(None, {}) is None


def test_structured_body_is_sent_on_the_first_structured_attempt(monkeypatch):
    calls = _patch(monkeypatch, [_parsed(V(ok=True))])
    e = _entry(no_thinking=NO_THINKING, structured_body=BUDGET,
               thinking={"formatter": {"chat_template_kwargs": {"enable_thinking": False, "keep": 1}}})
    out = llms._compat_model("qwen-test", e, 0.0, "formatter").with_structured_output(V).invoke("format this")
    assert out == V(ok=True) and len(calls) == 1
    assert calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False, "keep": 1}, **BUDGET}


def test_plain_calls_do_not_get_the_structured_body(monkeypatch):
    calls = _patch(monkeypatch, [AIMessage("prose", response_metadata={"finish_reason": "stop"})])
    llms._compat_model("qwen-test", _entry(structured_body=BUDGET), 0.4, "critic").invoke("review this")
    assert calls[0]["extra_body"] is None


# ── b. one retry path ──────────────────────────────────────────────────────


def test_validation_error_is_retried_once_with_thinking_off_and_same_limit(monkeypatch, capsys):
    calls = _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    e = _entry(no_thinking=NO_THINKING, structured_body=BUDGET)
    out = llms._compat_model("ds-test", e, 0.4, "critic").with_structured_output(V).invoke([HumanMessage("judge")])
    assert out == V(ok=True) and len(calls) == 2
    assert calls[0]["extra_body"] == BUDGET
    assert calls[1]["extra_body"] == {**BUDGET, **NO_THINKING}
    assert calls[1]["max_tokens"] == 32000
    assert llms._BREVITY in calls[1]["input"][-1].content
    err = capsys.readouterr().err
    assert "structured answer unusable (ValidationError" in err and "with thinking off" in err


def test_a_second_failure_raises_and_is_logged(monkeypatch, capsys):
    calls = _patch(monkeypatch, [_validation_error(), _validation_error()])
    m = llms._compat_model("ds-test", _entry(no_thinking=NO_THINKING), 0.4, "critic")
    with pytest.raises(ValidationError):
        m.with_structured_output(V).invoke("judge")
    assert len(calls) == 2
    assert "the structured retry failed too (ValidationError" in capsys.readouterr().err


def test_without_no_thinking_the_retry_gets_a_larger_limit(monkeypatch):
    calls = _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    llms._compat_model("ds-test", _entry(), 0.4, "critic").with_structured_output(V).invoke("judge")
    assert [c["max_tokens"] for c in calls] == [32000, 48000]
    assert calls[1]["extra_body"] is None


def test_max_tokens_retry_overrides_the_retry_limit(monkeypatch):
    calls = _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    e = _entry(no_thinking=NO_THINKING, max_tokens_retry=20000)
    llms._compat_model("ds-test", e, 0.4, "critic").with_structured_output(V).invoke("judge")
    assert [c["max_tokens"] for c in calls] == [32000, 20000]


def test_an_empty_structured_answer_is_retried_once_not_twice_nested(monkeypatch):
    """The invoke-level empty retry stays out of structured calls: two calls in all, not up to four."""
    empty = AIMessage("", response_metadata={"finish_reason": "stop"})
    calls = _patch(monkeypatch, [empty, _parsed(V(ok=True))])
    out = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
        .with_structured_output(V).invoke("judge")
    assert out == V(ok=True) and len(calls) == 2
    assert "response_format" in calls[0]["kwargs"]
    assert llms._EMPTY_NOTE not in str(calls[1]["input"])
    assert llms._BREVITY in str(calls[1]["input"])


def test_an_http_error_is_not_retried(monkeypatch):
    import httpx
    from openai import APIStatusError
    req = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    boom = APIStatusError("JSON schema conversion failed", response=httpx.Response(400, request=req), body=None)
    calls = _patch(monkeypatch, [boom])
    with pytest.raises(APIStatusError):
        llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
            .with_structured_output(V).invoke("judge")
    assert len(calls) == 1


def test_function_calling_with_no_tool_call_is_retried(monkeypatch):
    """function_calling: a reply with no tool call parses to None; that is an unusable answer, not a verdict."""
    from langchain_core.messages import ToolCall
    calls = _patch(monkeypatch, [
        AIMessage("I think it is fine.", response_metadata={"finish_reason": "stop"}),
        AIMessage("", tool_calls=[ToolCall(name="V", args={"ok": True}, id="c1")],
                  response_metadata={"finish_reason": "tool_calls"}),
    ])
    out = llms._compat_model("x-test", _entry(no_thinking=NO_THINKING, structured_output="function_calling"),
                             0.4, "critic").with_structured_output(V).invoke("judge")
    assert out == V(ok=True) and len(calls) == 2


# ── c. tool-call replies are not empty ─────────────────────────────────────


@pytest.mark.parametrize("field", ["tool_calls", "invalid_tool_calls"])
def test_a_tool_call_reply_is_not_an_empty_answer(monkeypatch, field):
    if field == "tool_calls":
        msg = AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "c1"}],
                        response_metadata={"finish_reason": "tool_calls"})
    else:
        msg = AIMessage("", invalid_tool_calls=[{"name": "read_file", "args": "{bad", "id": "c1", "error": "json",
                                                 "type": "invalid_tool_call"}],
                        response_metadata={"finish_reason": "tool_calls"})
    calls = _patch(monkeypatch, [msg])
    m = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 1.0, "generator")
    out = m.invoke([HumanMessage("research")], tools=[{"type": "function", "function": {"name": "read_file"}}])
    assert out is msg and len(calls) == 1


def test_a_tool_bound_plain_turn_keeps_the_empty_retry(monkeypatch):
    """The ReAct researcher binds tools for its prose turns too: an empty turn there is still asked once more."""
    calls = _patch(monkeypatch, [AIMessage("", response_metadata={"finish_reason": "length"}),
                                 AIMessage("the report", response_metadata={"finish_reason": "stop"})])
    m = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 1.0, "generator")
    out = m.invoke([HumanMessage("research")], tools=[{"type": "function", "function": {"name": "read_file"}}])
    assert out.text == "the report" and len(calls) == 2
    assert calls[1]["extra_body"] == NO_THINKING


# ── d. verdict checks on open-model answers ────────────────────────────────


def test_a_failing_step_verdict_with_no_reason_is_retried(monkeypatch):
    bare = StepVerdict(fulfilled=False, infeasible_requirements=[], infeasible_justification=None)
    good = StepVerdict(fulfilled=False, unmet_requirements=["the table has three columns, not four"])
    calls = _patch(monkeypatch, [_parsed(bare), _parsed(good)])
    out = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
        .with_structured_output(StepVerdict).invoke("judge")
    assert out == good and len(calls) == 2


def test_a_failing_execution_verdict_without_a_summary_is_used_as_it_is(monkeypatch):
    """execution_evaluator fills an empty error_summary from stderr; rejecting it cost a retry and could replace the
    model's failure_kind with the fallback's "other"."""
    bare = ExecutionVerdict(status="failure", failure_kind="missing_module")
    calls = _patch(monkeypatch, [_parsed(bare)])
    out = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
        .with_structured_output(ExecutionVerdict).invoke("judge")
    assert out == bare and len(calls) == 1 and llms.answer_mode(out) == "first_attempt"


def test_a_reasonless_verdict_on_the_retry_is_returned_not_raised(monkeypatch, capsys):
    """The check earns one retry; a reason-less answer to the retry is still a verdict (the researcher's step
    evaluator has no handler, and the self_debug one would spend two more calls to reach the same failure)."""
    bare = StepVerdict(fulfilled=False)
    calls = _patch(monkeypatch, [_parsed(bare), _parsed(StepVerdict(fulfilled=False))])
    out = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
        .with_structured_output(StepVerdict).invoke("judge")
    assert out == bare and len(calls) == 2
    assert llms.answer_mode(out) == "retry_no_thinking"
    assert "no reason is given" in llms.answer_problem(out)
    assert "answer used as it is, no retry left" in capsys.readouterr().err


def test_a_missing_answer_on_the_retry_still_raises(monkeypatch):
    calls = _patch(monkeypatch, [AIMessage("no tool call", response_metadata={"finish_reason": "stop"})] * 2)
    with pytest.raises(ValueError):
        llms._compat_model("x-test", _entry(no_thinking=NO_THINKING, structured_output="function_calling"),
                           0.4, "critic").with_structured_output(V).invoke("judge")
    assert len(calls) == 2


def test_verdict_rule():
    assert StepVerdict(fulfilled=True).structured_answer_problem() is None
    assert StepVerdict(fulfilled=False).structured_answer_problem()
    assert StepVerdict(fulfilled=False, feedback="   ").structured_answer_problem()
    assert StepVerdict(fulfilled=False, unmet_requirements=["  "]).structured_answer_problem()
    for kw in ({"unmet_requirements": ["x"]}, {"feedback": "x"}, {"suspected_fabrications": ["x"]},
               {"infeasible_requirements": ["x"]}):
        assert StepVerdict(fulfilled=False, **kw).structured_answer_problem() is None
    assert not hasattr(ExecutionVerdict(status="failure"), "structured_answer_problem")
    # constructing such verdicts in code (data_fixer, tests, old transcripts) stays valid
    StepVerdict(fulfilled=False)
    ExecutionVerdict(status="failure")


# ── answer modes, the retry's request fields, SDK resends, the classifier role ─


def test_answer_mode_names_the_attempt(monkeypatch):
    _patch(monkeypatch, [_parsed(V(ok=True))])
    out = llms._compat_model("qwen-test", _entry(no_thinking=NO_THINKING), 0.4, "critic") \
        .with_structured_output(V).invoke("judge")
    assert llms.answer_mode(out) == "first_attempt" and llms.answer_problem(out) is None
    _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    out = llms._compat_model("qwen-test", _entry(), 0.4, "critic").with_structured_output(V).invoke("judge")
    assert llms.answer_mode(out) == "retry_max_tokens"
    assert out == V(ok=True)                                # the tag is not a field: equality and dumps unchanged
    assert out.model_dump() == {"ok": True}
    assert llms.answer_mode(V(ok=True)) == "model"


def test_the_larger_limit_retry_keeps_the_structured_body(monkeypatch):
    calls = _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    llms._compat_model("ds-test", _entry(structured_body=BUDGET), 0.4, "critic").with_structured_output(V) \
        .invoke("judge")
    assert calls[0]["extra_body"] == BUDGET and calls[1]["extra_body"] == BUDGET
    assert [c["max_tokens"] for c in calls] == [32000, 48000]


def test_max_retries_and_timeout_come_from_the_registry():
    m = llms._compat_model("qwen-test", _entry(max_retries=0, timeout=1800), 0.4, "critic")
    assert m.max_retries == 0 and m.request_timeout == 1800.0
    assert m.root_client.max_retries == 0 and m.root_client.timeout == 1800.0
    d = llms._compat_model("qwen-test", _entry(), 0.4, "critic")
    assert d.root_client.max_retries == 2                    # the SDK default when the registry says nothing


def test_model_failure_types():
    from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError, \
        LengthFinishReasonError, NotFoundError, RateLimitError
    types = llms.model_failure_types()
    for t in (ValueError, LengthFinishReasonError, APITimeoutError, InternalServerError):
        assert any(issubclass(t, k) for k in types)
    for t in (AuthenticationError, NotFoundError, RateLimitError, APIConnectionError):
        assert not any(issubclass(t, k) for k in types)


def test_the_intent_gate_is_a_classifier_not_a_formatter(registry):
    registry({"qwen-test": _entry(thinking={"formatter": NO_THINKING})})
    assert llms.chat_model("qwen-test", "formatter").extra_body == NO_THINKING
    assert llms.chat_model("qwen-test", "classifier").extra_body is None
    assert llms.chat_model("qwen-test", "classifier").temperature == 0.0
    import inspect
    from cmbagent_lg.self_debug import preflight
    assert '"classifier")' in inspect.getsource(preflight._classify_intent)


# ── g. the effective entry is logged once per (model, role), never the key ─


def test_effective_entry_logged_once_per_model_and_role(registry, monkeypatch, capsys):
    monkeypatch.setenv("TEST_SECRET_KEY", "sk-very-secret-123")
    registry({"qwen-log": _entry(api_key_env="TEST_SECRET_KEY", no_thinking=NO_THINKING, structured_body=BUDGET,
                                 thinking={"formatter": NO_THINKING})})
    llms._LOGGED_ENTRIES.discard(("qwen-log", "formatter"))
    llms._LOGGED_ENTRIES.discard(("qwen-log", "critic"))
    llms.chat_model("qwen-log", "formatter")
    llms.chat_model.cache_clear()
    llms.chat_model("qwen-log", "formatter")
    llms.chat_model("qwen-log", "critic")
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[llms] qwen-log")]
    assert len(lines) == 2
    assert "max_tokens=32000" in lines[0] and "reasoning_budget_tokens" in lines[0]
    assert 'thinking[formatter]={"chat_template_kwargs": {"enable_thinking": false}}' in lines[0]
    assert "thinking[critic]=null" in lines[1]
    assert "sk-very-secret-123" not in err


# ── h. the endpoint-wide extra_body layer ─────────────────────────────────

NO_CACHE = {"cache": {"no-cache": True, "no-store": True}}


def test_extra_body_reaches_every_role(registry):
    registry({"gw-test": _entry(extra_body=NO_CACHE, thinking={"formatter": NO_THINKING})})
    assert llms.chat_model("gw-test", "critic").extra_body == NO_CACHE
    assert llms.chat_model("gw-test", "coder").extra_body == NO_CACHE
    assert llms.chat_model("gw-test", "formatter").extra_body == {**NO_CACHE, **NO_THINKING}


def test_extra_body_is_sent_on_plain_structured_and_retry_calls(monkeypatch):
    e = _entry(extra_body=NO_CACHE, no_thinking=NO_THINKING, structured_body=BUDGET)
    calls = _patch(monkeypatch, [AIMessage("prose", response_metadata={"finish_reason": "stop"})])
    llms._compat_model("gw-test", e, 0.4, "critic").invoke("review this")
    assert calls[0]["extra_body"] == NO_CACHE
    calls = _patch(monkeypatch, [_validation_error(), _parsed(V(ok=True))])
    llms._compat_model("gw-test", e, 0.4, "critic").with_structured_output(V).invoke("judge")
    assert calls[0]["extra_body"] == {**NO_CACHE, **BUDGET}
    assert calls[1]["extra_body"] == {**NO_CACHE, **BUDGET, **NO_THINKING}
    calls = _patch(monkeypatch, [AIMessage("", response_metadata={"finish_reason": "length"}),
                                 AIMessage("answer", response_metadata={"finish_reason": "stop"})])
    llms._compat_model("gw-test", e, 0.4, "critic").invoke("write")
    assert calls[1]["extra_body"] == {**NO_CACHE, **NO_THINKING}


def test_later_layers_win_key_by_key_over_extra_body(monkeypatch):
    base = {"chat_template_kwargs": {"enable_thinking": True, "keep": 1}, "cache": {"no-cache": True}}
    e = _entry(extra_body=base, thinking={"formatter": NO_THINKING})
    m = llms._compat_model("gw-test", e, 0.0, "formatter")
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": False, "keep": 1}, "cache": {"no-cache": True}}
    assert base["chat_template_kwargs"]["enable_thinking"] is True          # the registry entry is not mutated


def test_extra_body_must_be_an_object(registry):
    registry({"gw-test": _entry(extra_body=["no"])})
    with pytest.raises(ValueError, match="extra_body must be a JSON object"):
        llms.chat_model("gw-test", "critic")


def test_extra_body_and_model_are_in_the_effective_entry_line(registry, capsys):
    registry({"gw-log": _entry(model="org/Model-1", extra_body=NO_CACHE)})
    llms._LOGGED_ENTRIES.discard(("gw-log", "critic"))
    llms.chat_model("gw-log", "critic")
    line = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[llms] gw-log")][0]
    assert 'extra_body={"cache": {"no-cache": true, "no-store": true}}' in line
    assert "model=org/Model-1" in line
