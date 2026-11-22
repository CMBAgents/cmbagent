"""A structured answer that stops at the output limit is retried once with a larger budget and a brevity note.

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
    def boom(x):
        raise ValueError("schema mismatch")

    r = llms._with_length_retry(RunnableLambda(boom), RunnableLambda(lambda x: "never"))
    try:
        r.invoke("x")
    except ValueError:
        pass
    else:
        raise AssertionError("a non-length error must propagate")


def test_compat_model_wraps_structured_output():
    e = {"base_url": "http://127.0.0.1:1/v1", "max_tokens": 16000}
    m = llms._compat_model("deepseek-test", e, 0.2)
    from pydantic import BaseModel

    class V(BaseModel):
        ok: bool

    r = m.with_structured_output(V)
    assert hasattr(r, "fallbacks") and len(r.fallbacks) == 1
    assert llms._add_brevity("x").endswith(llms._BREVITY)
