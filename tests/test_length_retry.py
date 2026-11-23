"""An unusable structured answer (output limit reached, or an answer that does not parse) is retried once with a
brevity note: thinking off at the same limit when the registry gives `no_thinking`, else a larger limit.

    PYTHONPATH=. python -m pytest tests/test_length_retry.py
"""
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from openai import LengthFinishReasonError

from cmbagent_lg import llms


class _Overflow(LengthFinishReasonError):
    def __init__(self):  # the real constructor needs a completion object
        Exception.__init__(self, "length limit reached")


def test_retry_only_on_length_overflow():
    calls = []

    def first(x):
        calls.append(("first", x))
        raise _Overflow()

    def second(x):
        calls.append(("second", x))
        return {"ok": True}

    r = llms._with_length_retry(RunnableLambda(first), RunnableLambda(llms._add_brevity) | RunnableLambda(second))
    assert r.invoke([HumanMessage("judge this")]) == {"ok": True}
    assert [c[0] for c in calls] == ["first", "second"]
    assert llms._BREVITY in calls[1][1][-1].content


def test_other_errors_are_not_retried():
    """A schema mismatch (ValueError) is retried; an error that is neither an overflow nor a bad answer is not."""
    def boom(x):
        raise TypeError("a bug, not an answer")

    r = llms._with_length_retry(RunnableLambda(boom), RunnableLambda(lambda x: "never"))
    try:
        r.invoke("x")
    except TypeError:
        pass
    else:
        raise AssertionError("an error that is neither an overflow nor an unusable answer must propagate")


def test_unparsable_answer_is_retried():
    def boom(x):
        raise ValueError("schema mismatch")

    r = llms._with_length_retry(RunnableLambda(boom), RunnableLambda(lambda x: "second"))
    assert r.invoke("x") == "second"


def test_compat_model_wraps_structured_output():
    e = {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 16000}
    m = llms._compat_model("deepseek-test", e, 0.2)
    from pydantic import BaseModel

    class V(BaseModel):
        ok: bool

    r = m.with_structured_output(V)
    assert r.name == "structured_retry"
    assert llms._add_brevity("x").endswith(llms._BREVITY)


def _fake_invoke(replies, calls):
    from langchain_core.messages import AIMessage

    def invoke(self, input, config=None, **kwargs):
        calls.append({"extra_body": self.extra_body, "input": input})
        text, reason = replies[len(calls) - 1]
        return AIMessage(text, response_metadata={"finish_reason": reason})
    return invoke


def test_empty_answer_is_asked_once_more_with_thinking_off(monkeypatch):
    from langchain_openai import ChatOpenAI
    calls = []
    monkeypatch.setattr(ChatOpenAI, "invoke", _fake_invoke([("", "length"), ("print(1)", "stop")], calls))
    e = {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 32000,
         "no_thinking": {"chat_template_kwargs": {"enable_thinking": False}}}
    m = llms._compat_model("qwen-test", e, 0.2)
    out = m.invoke([HumanMessage("write the script")])
    assert out.text == "print(1)" and len(calls) == 2
    assert calls[0]["extra_body"] is None
    assert calls[1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert calls[1]["input"][-1].content == llms._EMPTY_NOTE


def test_empty_retry_without_no_thinking_and_only_once(monkeypatch):
    from langchain_openai import ChatOpenAI
    calls = []
    monkeypatch.setattr(ChatOpenAI, "invoke", _fake_invoke([("", "length"), ("", "length")], calls))
    m = llms._compat_model("ds-test", {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 32000}, 0.2)
    out = m.invoke("write the script")
    assert out.text == "" and len(calls) == 2           # a second empty answer is returned as is
    assert calls[1]["input"].endswith(llms._EMPTY_NOTE)


def test_nonempty_answer_is_not_retried(monkeypatch):
    from langchain_openai import ChatOpenAI
    calls = []
    monkeypatch.setattr(ChatOpenAI, "invoke", _fake_invoke([("ok", "stop")], calls))
    m = llms._compat_model("ds-test", {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 32000}, 0.2)
    assert m.invoke("x").text == "ok" and len(calls) == 1



def test_structured_retry_turns_thinking_off(monkeypatch):
    """The step evaluator's overflow: the retry runs with the registry's no_thinking fields and the same limit (thinking
    off needs no larger one)."""
    from langchain_core.messages import AIMessage
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel
    seen = []

    def invoke(self, input, config=None, **kwargs):
        seen.append({"extra_body": self.extra_body, "max_tokens": self.max_tokens, "input": input})
        if len(seen) == 1:
            raise _Overflow()
        return AIMessage('{"ok": true}', additional_kwargs={"parsed": V(ok=True)})
    monkeypatch.setattr(ChatOpenAI, "invoke", invoke)

    class V(BaseModel):
        ok: bool

    e = {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 32000,
         "no_thinking": {"chat_template_kwargs": {"enable_thinking": False}}}
    res = llms._compat_model("ds-test", e, 0.2).with_structured_output(V).invoke([HumanMessage("judge this step")])
    assert res == V(ok=True) and len(seen) == 2
    assert seen[0]["extra_body"] is None and seen[0]["max_tokens"] == 32000
    assert seen[1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert seen[1]["max_tokens"] == 32000
    assert llms._BREVITY in seen[1]["input"][-1].content
