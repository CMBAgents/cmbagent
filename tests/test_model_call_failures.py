"""A model call that times out or gets a 5xx no longer ends the run on its first failure.

- `llms.invoke_with_transient_retry`: one retry after a pause; an authentication, not-found, rate-limit (429), quota or
  connection error raises at once.
- engineer: a second failure fails only the attempt, with a script that exits with the reason; two such failures in a
  row end the step with a recorded reason (`engineer_model_failed`, logs/step_N_engineer_errors.json, the step
  outcome), and the step is not sent to the replanner.
- planner, plan reviewer, researcher: a second failure ends the run with `llms.ModelCallFailed`, whose message says
  which call failed and why; inside deep_research the researcher's ends the run as a failed step with that reason.

Evidence: Kimi m15-1 r33, where an OpenAITimeoutError in the engineer ended the run. No model is called: every model
is a stub, and the scripts run in a subprocess.

    PYTHONPATH=. python -m pytest tests/test_model_call_failures.py
"""
import json
import types

import httpx
import pytest

from cmbagent_lg import llms
from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.schemas import ExecutionVerdict, StepVerdict

SCRIPT = 'if __name__ == "__main__":\n    print("result: 42")\n'
_REQ = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")


def _status_error(cls, code):
    return cls("boom", response=httpx.Response(code, request=_REQ), body=None)


def _timeout():
    from openai import APITimeoutError
    return APITimeoutError(request=_REQ)


def _transient_errors():
    from openai import InternalServerError
    return [_timeout(), _status_error(InternalServerError, 503)]


def _fatal_errors():
    from openai import APIConnectionError, AuthenticationError, NotFoundError, RateLimitError
    return [_status_error(AuthenticationError, 401), _status_error(NotFoundError, 404),
            _status_error(RateLimitError, 429), APIConnectionError(request=_REQ), RuntimeError("quota exceeded")]


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    pauses = []
    monkeypatch.setattr("time.sleep", lambda s: pauses.append(s))
    return pauses


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(agent="engineer"):
    return Step(sub_task="print the answer", sub_task_agent=agent, bullet_points=["print the result"])


# ── the retry helper ────────────────────────────────────────────────────────


@pytest.mark.parametrize("err", _transient_errors(), ids=lambda e: type(e).__name__)
def test_a_timeout_or_5xx_is_tried_once_more_after_a_pause(err, _no_pause, capsys):
    calls = []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise err
        return "answer"

    assert llms.invoke_with_transient_retry(call, "test call") == "answer"
    assert len(calls) == 2 and _no_pause == [llms.TRANSIENT_RETRY_PAUSE_S]
    assert "test call: model call failed" in capsys.readouterr().err

    calls.clear()

    def always():
        calls.append(1)
        raise err

    with pytest.raises(type(err)):
        llms.invoke_with_transient_retry(always, "test call")
    assert len(calls) == 2


@pytest.mark.parametrize("err", _fatal_errors(), ids=lambda e: type(e).__name__)
def test_configuration_and_rate_limit_errors_raise_at_once(err, _no_pause):
    calls = []

    def call():
        calls.append(1)
        raise err

    with pytest.raises(type(err)):
        llms.invoke_with_transient_retry(call, "test call")
    assert len(calls) == 1 and _no_pause == []
    assert not isinstance(err, llms.transient_model_errors())


def test_gemini_and_anthropic_server_errors_are_transient():
    from anthropic import APITimeoutError as AnthropicTimeout
    from google.genai.errors import ServerError
    types_ = llms.transient_model_errors()
    assert issubclass(ServerError, types_) and issubclass(AnthropicTimeout, types_)
    assert issubclass(httpx.ReadTimeout, types_) and not issubclass(httpx.ConnectError, types_)
    # The evaluators absorb the same errors as before, and these.
    assert set(types_) <= set(llms.model_failure_types())


def test_model_call_failed_reads_as_a_reason():
    e = llms.ModelCallFailed("planner", _timeout())
    assert str(e).startswith("the planner model call failed twice, after one retry (APITimeoutError:")
    assert str(e).endswith("the run ends here")
    assert e.record() == {"role": "planner", "error": e.error, "reason": str(e)}


# ── the engineer ────────────────────────────────────────────────────────────


def _run_graph(tmp_path, monkeypatch, replies, max_attempts=3):
    """Run the self_debug graph with `replies` for the engineer's model calls (an exception to raise, or the script's
    answer text), in turn. Returns (final state, number of engineer calls)."""
    from cmbagent_lg.self_debug.graph import graph

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    calls = []

    class _Proposer:
        def invoke(self, msgs, config=None):
            calls.append(msgs[0].content)
            r = replies[min(len(calls) - 1, len(replies) - 1)]
            if isinstance(r, BaseException):
                raise r
            return types.SimpleNamespace(text=r)

    class _Critic:
        """A script that raises SystemExit failed; anything else ran, and meets the step's goal."""

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, msgs, config=None):
            if self.schema is ExecutionVerdict:
                if "SystemExit" in msgs[0].content:
                    return ExecutionVerdict(status="failure", error_summary="exited non-zero", failure_kind="other")
                return ExecutionVerdict(status="success")
            return StepVerdict(fulfilled=True)

    critic = _Critic()
    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: critic)
    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False, vlm_enabled=False,
                      max_n_attempts=max_attempts)
    out = graph.invoke({"step": _step(), "step_number": 1, "work_dir": str(tmp_path)}, context=ctx)
    return out, len(calls)


GOOD = "**Python Code:**\n```python\n" + SCRIPT + "```\n"


def test_one_failed_engineer_call_fails_only_the_attempt(tmp_path, monkeypatch):
    # attempt 1: the call and its retry time out; attempt 2 answers.
    out, n_calls = _run_graph(tmp_path, monkeypatch, [_timeout(), _timeout(), GOOD])
    assert n_calls == 3 and out["attempts"] == 2
    assert out["current_step_verdict"].fulfilled
    assert out["engineer_model_failures"] == 0 and out["engineer_model_failures_total"] == 1
    assert not out.get("engineer_model_failed")
    # The failed attempt ran a script that exits with the reason, kept like any failed attempt.
    failed = (tmp_path / "codebase" / "step_1_failure_1.py").read_text()
    assert "raise SystemExit(" in failed and "the engineer model call failed twice" in failed
    log = (tmp_path / "codebase" / "step_1_failure_1.log").read_text()
    assert "APITimeoutError" in log and "static check" not in log      # it ran: the static check let it through
    rec = json.loads((tmp_path / "logs" / "step_1_engineer_errors.json").read_text())
    assert [(r["attempt"], r["consecutive"], r["step_failed"]) for r in rec] == [(1, 1, False)]


def test_two_failures_in_a_row_end_the_step_with_a_reason(tmp_path, monkeypatch):
    out, n_calls = _run_graph(tmp_path, monkeypatch, [_timeout()], max_attempts=5)
    assert n_calls == 4 and out["attempts"] == 2           # two attempts, each a call and its retry
    v = out["current_step_verdict"]
    assert v.fulfilled is False and "failed 2 times in a row" in v.feedback and "APITimeoutError" in v.feedback
    assert out["engineer_model_failed"] == v.feedback
    rec = json.loads((tmp_path / "logs" / "step_1_engineer_errors.json").read_text())
    assert [(r["attempt"], r["consecutive"], r["step_failed"]) for r in rec] == [(1, 1, False), (2, 2, True)]
    # Only the first failure ran a failing script; the second ended the step before format_engineer.
    assert (tmp_path / "codebase" / "step_1_failure_1.py").exists()
    assert not (tmp_path / "codebase" / "step_1_failure_2.py").exists()


def test_failures_that_are_not_consecutive_do_not_end_the_step(tmp_path, monkeypatch):
    # A failed call, a script that fails on its own, a failed call, an answer: never two failures in a row.
    bad = "**Python Code:**\n```python\nif __name__ == \"__main__\":\n    raise SystemExit('no')\n```\n"
    out, n_calls = _run_graph(tmp_path, monkeypatch,
                              [_timeout(), _timeout(), bad, _timeout(), _timeout(), GOOD], max_attempts=4)
    assert out["attempts"] == 4 and out["current_step_verdict"].fulfilled
    assert out["engineer_model_failures_total"] == 2 and not out.get("engineer_model_failed")


@pytest.mark.parametrize("err", _fatal_errors(), ids=lambda e: type(e).__name__)
def test_a_configuration_error_in_the_engineer_still_ends_the_run(tmp_path, monkeypatch, err):
    with pytest.raises(type(err)):
        _run_graph(tmp_path, monkeypatch, [err])


def test_route_after_engineer():
    ctx = PlanContext(main_task="t")
    assert sd_nodes.route_after_engineer({"engineer_model_failed": "why"}, _rt(ctx)) == sd_nodes.END
    assert sd_nodes.route_after_engineer({}, _rt(ctx)) == "format_engineer"


# ── deep_research: the step outcome ─────────────────────────────────────────


def _run_step(tmp_path, monkeypatch, sub_state=None, raises=None, agent="engineer"):
    from cmbagent_lg.deep_research import nodes as dr

    class _G:
        def invoke(self, inp, context=None):
            if raises is not None:
                raise raises
            return sub_state

    monkeypatch.setattr(dr, "self_debug_graph", _G())
    monkeypatch.setattr(dr, "researcher_graph", _G())
    plan = Plan(sub_tasks=[_step(agent), _step("researcher")])
    ctx = PlanContext(main_task="t")
    out = dr.run_step({"plan": plan, "step_index": 1, "work_dir": str(tmp_path)}, _rt(ctx))
    return out, dr, ctx


def test_the_engineer_failure_is_the_step_reason_and_is_not_replanned(tmp_path, monkeypatch):
    why = "the engineer model call failed 2 times in a row (each after one retry); last error: APITimeoutError: x"
    sub = {"attempts": 2, "engineer_model_failed": why, "engineer_model_failures_total": 2,
           "current_step_verdict": StepVerdict(fulfilled=False, unmet_requirements=[why], feedback=why),
           # a stale execution verdict from the first attempt must not become the reason
           "current_execution_verdict": ExecutionVerdict(status="failure", error_summary="stale")}
    out, dr, ctx = _run_step(tmp_path, monkeypatch, sub_state=sub)
    o = out["step_outcomes"][-1]
    assert o["fulfilled"] is False and o["reason"] == why and o["engineer_model_errors"] == 2
    assert o["model_failure"] == {"role": "engineer", "reason": why} and not o.get("exhausted")
    assert dr._step_exhausted({**sub, "attempts": 3}, _step(), PlanContext(main_task="t", max_n_attempts=3)) is False
    status = json.loads((tmp_path / "logs" / "analysis_status.json").read_text())
    assert status["status"] == "halted" and status["reason"] == why


def test_a_researcher_model_failure_ends_the_run_as_a_failed_step(tmp_path, monkeypatch):
    err = llms.ModelCallFailed("researcher", _timeout())
    out, dr, ctx = _run_step(tmp_path, monkeypatch, raises=err, agent="researcher")
    o = out["step_outcomes"][-1]
    assert o["fulfilled"] is False and o["reason"] == str(err) and o["model_failure"]["role"] == "researcher"
    assert len(out["step_summaries"]) == len(out["step_outcomes"]) == 1
    status = json.loads((tmp_path / "logs" / "analysis_status.json").read_text())
    assert status["status"] == "halted" and "researcher model call failed twice" in status["reason"]
    state = {"plan": Plan(sub_tasks=[_step("researcher"), _step("researcher")]), **out}
    assert dr.route_after_review(state, _rt(ctx)) == dr.END


# ── planner, plan reviewer, researcher ──────────────────────────────────────


class _Failing:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def invoke(self, msgs, config=None):
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, BaseException):
            raise r
        return types.SimpleNamespace(text=r, content=r)


def _planning_ctx():
    return PlanContext(main_task="t")


def test_the_planner_retries_once_then_ends_the_run_with_a_reason(monkeypatch, capsys):
    from cmbagent_lg.planning import nodes as pn
    m = _Failing([_timeout(), "the plan"])
    monkeypatch.setattr(pn, "_proposer", lambda _c: m)
    assert pn.planner({}, _rt(_planning_ctx()))["raw_plan"] == "the plan" and m.calls == 2

    m = _Failing([_timeout()])
    monkeypatch.setattr(pn, "_proposer", lambda _c: m)
    with pytest.raises(llms.ModelCallFailed) as ei:
        pn.planner({}, _rt(_planning_ctx()))
    assert m.calls == 2 and "the planner model call failed twice" in str(ei.value)
    assert "[planner] the planner model call failed twice" in capsys.readouterr().err

    from openai import RateLimitError
    m = _Failing([_status_error(RateLimitError, 429)])
    monkeypatch.setattr(pn, "_proposer", lambda _c: m)
    with pytest.raises(RateLimitError):
        pn.planner({}, _rt(_planning_ctx()))
    assert m.calls == 1


def test_the_plan_reviewer_retries_once_then_ends_the_run_with_a_reason(monkeypatch):
    from openai import InternalServerError
    from cmbagent_lg.planning import nodes as pn
    plan = Plan(sub_tasks=[_step()])
    m = _Failing([_status_error(InternalServerError, 502)])
    monkeypatch.setattr(pn, "_critic", lambda _c: m)
    with pytest.raises(llms.ModelCallFailed) as ei:
        pn.plan_reviewer({"current_plan": plan, "round": 1}, _rt(_planning_ctx()))
    assert m.calls == 2 and ei.value.role == "plan reviewer" and "InternalServerError" in str(ei.value)


@pytest.mark.parametrize("mode", ["plain", "react"])
def test_the_researcher_retries_once_then_ends_the_run_with_a_reason(tmp_path, monkeypatch, mode):
    from cmbagent_lg.researcher import nodes as rn
    from cmbagent_lg.researcher import react_researcher as rr

    class _Bindable(_Failing):
        def bind_tools(self, tools):
            return self

    ctx = PlanContext(main_task="t", researcher_mode=mode, vlm_enabled=False)
    state = {"step": _step("researcher"), "step_number": 1, "work_dir": str(tmp_path)}
    node = rr.react_researcher if mode == "react" else rn.researcher
    m = _Bindable([_timeout(), "## Report\n\nThe answer is 42."])
    monkeypatch.setattr(rn, "_proposer", lambda _c: m)
    monkeypatch.setattr(rr, "_proposer", lambda _c: m, raising=False)
    assert "42" in node(state, _rt(ctx))["current_report"] and m.calls == 2

    m = _Bindable([_timeout()])
    monkeypatch.setattr(rn, "_proposer", lambda _c: m)
    monkeypatch.setattr(rr, "_proposer", lambda _c: m, raising=False)
    with pytest.raises(llms.ModelCallFailed) as ei:
        node(state, _rt(ctx))
    # Two calls, not four: the react writer does not fall back to the plain writer's same call.
    assert m.calls == 2 and ei.value.role == "researcher"


def test_a_failure_on_the_last_allowed_attempt_is_the_reason_and_is_not_replanned(tmp_path, monkeypatch):
    # attempt 1 fails on its own, attempt 2 (the cap) has its call and retry time out: the attempt cap ends the
    # step after one model failure, so `engineer_model_failed` is never set.
    bad = "**Python Code:**\n```python\nif __name__ == \"__main__\":\n    raise SystemExit('no')\n```\n"
    sub, n_calls = _run_graph(tmp_path, monkeypatch, [bad, _timeout(), _timeout()], max_attempts=2)
    assert n_calls == 3 and sub["attempts"] == 2 and not sub.get("engineer_model_failed")
    assert sub["engineer_model_failures"] == 1 and "APITimeoutError" in sub["engineer_model_last_error"]
    from cmbagent_lg.deep_research import nodes as dr
    ctx = PlanContext(main_task="t", max_n_attempts=2)
    assert dr._step_exhausted(sub, _step(), ctx) is False
    out, dr, _ctx = _run_step(tmp_path, monkeypatch, sub_state=sub)
    o = out["step_outcomes"][-1]
    assert o["fulfilled"] is False and not o.get("exhausted")
    assert o["reason"] == sub["engineer_model_last_error"] and o["model_failure"]["role"] == "engineer"
    # An answered call resets the count: a later ordinary failure on the cap is replanned as before.
    assert dr._step_exhausted({**sub, "engineer_model_failures": 0}, _step(), ctx) is True
