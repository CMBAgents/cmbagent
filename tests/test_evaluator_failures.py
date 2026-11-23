"""A failing evaluator model call no longer ends the run.

- execution_evaluator falls back to a verdict decided from the run (returncode 0, no timeout, no Traceback in stderr);
- step_evaluator returns an `evaluator_error` outcome: the same run is judged again, no attempt is used and nothing
  reaches the engineer; two consecutive errors fail the attempt closed.

Only an unusable answer, a timeout or a 5xx is absorbed (`llms.model_failure_types`): an authentication, not-found or
connection error still ends the run. Each verdict records what produced it (`answer_mode`) in its log file, in state,
and in the step outcome.

No model is called: the proposer, formatter and critic are stubs, the scripts run in a subprocess.

    PYTHONPATH=. python -m pytest tests/test_evaluator_failures.py
"""
import json
import types

import pytest

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, StepVerdict

SCRIPT = 'if __name__ == "__main__":\n    print("result: 42")\n'


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step():
    return Step(sub_task="print the answer", sub_task_agent="engineer", bullet_points=["print the result"])


class _Boom(ValueError):
    """An unusable answer, as the structured retry raises it (ValidationError, OutputParserException: ValueError)."""


# ── execution_evaluator: deterministic fallback ─────────────────────────────


@pytest.mark.parametrize("rc,timed_out,stderr,status", [
    (0, False, "", "success"),
    (0, False, "UserWarning: something\n", "success"),
    (0, False, "Traceback (most recent call last):\n  ...\nValueError: bad\n", "failure"),
    (1, False, "boom\n", "failure"),
    (0, True, "", "failure"),
])
def test_deterministic_execution_verdict(rc, timed_out, stderr, status):
    v = sd_nodes.deterministic_execution_verdict({"execution_returncode": rc, "execution_timed_out": timed_out,
                                                  "execution_stderr": stderr, "execution_elapsed_s": 120.0})
    assert v.status == status
    if status == "failure":
        assert v.error_summary and v.failure_kind == "other"


def test_execution_evaluator_falls_back_when_the_model_fails(tmp_path, monkeypatch, capsys):
    class _Critic:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            raise _Boom("LengthFinishReasonError after the retry")

    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())
    state = {"step": _step(), "step_number": 1, "attempts": 1, "work_dir": str(tmp_path),
             "current_code": EngineerResponse(python_code=SCRIPT, code_explanation="x"),
             "execution_stdout": "", "execution_stderr": "Traceback (most recent call last):\nKeyError: 'z'\n",
             "execution_returncode": 1, "execution_timed_out": False}
    out = sd_nodes.execution_evaluator(state, _rt(PlanContext(main_task="t")))
    v = out["current_execution_verdict"]
    assert v.status == "failure" and "KeyError: 'z'" in v.error_summary
    assert out["execution_verdict_fallbacks"] == 1
    assert out["error_history"] == [v.error_summary]
    assert "EVALUATOR ERROR" in capsys.readouterr().err
    assert out["execution_verdict_modes"] == ["deterministic_fallback"]
    saved = json.loads((tmp_path / "logs" / "step_1_execution_verdict.json").read_text())
    assert saved["answer_mode"] == "deterministic_fallback" and saved["status"] == "failure"


# ── step_evaluator: evaluator_error outcome ─────────────────────────────────


def _run_graph(tmp_path, monkeypatch, step_replies, max_attempts=3):
    """Run the self_debug graph with a clean script and `step_replies` for the step evaluator (a StepVerdict, or an
    exception to raise), in turn. Returns (final state, engineer prompts, step-evaluator call count)."""
    from cmbagent_lg.self_debug.graph import graph

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    prompts, step_calls = [], []

    class _Proposer:
        def invoke(self, msgs, config=None):
            prompts.append(msgs[0].content)
            return types.SimpleNamespace(text="**Python Code:**\n```python\n" + SCRIPT + "```\n")

    class _Critic:
        def __init__(self):
            self.schema = None

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, msgs, config=None):
            if self.schema is ExecutionVerdict:
                return ExecutionVerdict(status="success")
            step_calls.append(1)
            r = step_replies[min(len(step_calls) - 1, len(step_replies) - 1)]
            if isinstance(r, BaseException):
                raise r
            return r

    critic = _Critic()
    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: critic)
    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False, vlm_enabled=False,
                      max_n_attempts=max_attempts)
    out = graph.invoke({"step": _step(), "step_number": 1, "work_dir": str(tmp_path)}, context=ctx)
    return out, prompts, len(step_calls)


def test_one_evaluator_error_judges_the_same_run_again(tmp_path, monkeypatch):
    out, prompts, n_calls = _run_graph(tmp_path, monkeypatch, [_Boom("no verdict"), StepVerdict(fulfilled=True)])
    assert n_calls == 2
    assert out["attempts"] == 1 and len(prompts) == 1       # no attempt used, the engineer was not asked again
    assert out["current_step_verdict"].fulfilled
    assert out["step_evaluator_outcome"] == "verdict"
    assert out["step_evaluator_errors"] == 0 and out["step_evaluator_errors_total"] == 1
    assert not out.get("step_feedback_history")
    rec = json.loads((tmp_path / "logs" / "step_1_evaluator_errors.json").read_text())
    assert [r["outcome"] for r in rec] == ["judged_again"]


def test_two_consecutive_errors_fail_closed_and_the_next_attempt_runs(tmp_path, monkeypatch):
    out, prompts, n_calls = _run_graph(
        tmp_path, monkeypatch, [_Boom("a"), _Boom("b"), StepVerdict(fulfilled=True)])
    assert n_calls == 3
    assert out["attempts"] == 2 and len(prompts) == 2       # the failed-closed attempt counts; the next one passes
    assert out["current_step_verdict"].fulfilled
    assert sd_nodes._EVALUATOR_FAILED_FEEDBACK in prompts[1]
    assert not out.get("step_feedback_history")              # not recorded as the evaluator's own feedback
    rec = json.loads((tmp_path / "logs" / "step_1_evaluator_errors.json").read_text())
    assert [r["outcome"] for r in rec] == ["judged_again", "failed_closed"]
    assert (tmp_path / "codebase" / "step_1_failure_1.py").exists()


def test_a_persistently_failing_evaluator_ends_the_step_without_a_crash(tmp_path, monkeypatch):
    out, prompts, n_calls = _run_graph(tmp_path, monkeypatch, [_Boom("always")], max_attempts=2)
    # attempt 1: error, error -> failed closed; attempt 2: still in a row -> failed closed at once
    assert n_calls == 3 and out["attempts"] == 2
    assert out["current_step_verdict"].fulfilled is False
    assert out["step_evaluator_outcome"] == "failed_closed" and out["step_evaluator_errors_total"] == 3


def test_route_after_step_evaluator_on_error():
    ctx = PlanContext(main_task="t", max_n_attempts=1)
    st = {"step_evaluator_outcome": "evaluator_error", "attempts": 1,
          "current_step_verdict": StepVerdict(fulfilled=False, feedback="stale")}
    assert sd_nodes.route_after_step_evaluator(st, _rt(ctx)) == "step_evaluator"


# ── which errors are absorbed ───────────────────────────────────────────────


def _status_error(cls, code):
    import httpx
    req = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    return cls("boom", response=httpx.Response(code, request=req), body=None)


def _config_errors():
    import httpx
    from openai import APIConnectionError, AuthenticationError, NotFoundError, RateLimitError
    req = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    return [_status_error(AuthenticationError, 401), _status_error(NotFoundError, 404),
            _status_error(RateLimitError, 429), APIConnectionError(request=req), RuntimeError("quota")]


def _absorbed_errors():
    import httpx
    from openai import APITimeoutError, InternalServerError
    req = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
    return [_Boom("bad answer"), APITimeoutError(request=req), _status_error(InternalServerError, 500)]


@pytest.mark.parametrize("err", _config_errors(), ids=lambda e: type(e).__name__)
def test_a_configuration_error_still_ends_the_run(tmp_path, monkeypatch, err):
    with pytest.raises(type(err)):
        _run_graph(tmp_path, monkeypatch, [err])

    class _Critic:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            raise err

    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())
    state = {"step": _step(), "step_number": 1, "attempts": 1, "work_dir": str(tmp_path),
             "current_code": EngineerResponse(python_code=SCRIPT, code_explanation="x"),
             "execution_stdout": "result: 42\n", "execution_stderr": "", "execution_returncode": 0,
             "execution_timed_out": False}
    with pytest.raises(type(err)):
        sd_nodes.execution_evaluator(state, _rt(PlanContext(main_task="t")))


@pytest.mark.parametrize("err", _absorbed_errors(), ids=lambda e: type(e).__name__)
def test_an_unusable_answer_timeout_or_5xx_is_absorbed(tmp_path, monkeypatch, err):
    out, _prompts, n_calls = _run_graph(tmp_path, monkeypatch, [err, StepVerdict(fulfilled=True)])
    assert n_calls == 2 and out["current_step_verdict"].fulfilled


# ── what produced each verdict ──────────────────────────────────────────────


def test_answer_modes_reach_the_verdict_files_state_and_outcome(tmp_path, monkeypatch):
    from cmbagent_lg.deep_research.nodes import evaluator_outcome_fields
    from cmbagent_lg.llms import _tag

    retried = _tag(StepVerdict(fulfilled=True), "retry_no_thinking")
    out, _prompts, _n = _run_graph(tmp_path, monkeypatch, [_Boom("a"), _Boom("b"), retried], max_attempts=3)
    assert out["step_verdict_modes"] == ["failed_closed", "retry_no_thinking"]
    assert out["execution_verdict_modes"] == ["model", "model"]
    assert json.loads((tmp_path / "logs" / "step_1_verdict.json").read_text())["answer_mode"] == "retry_no_thinking"
    assert json.loads((tmp_path / "logs" / "step_1_verdict_failure_1.json").read_text())["answer_mode"] \
        == "failed_closed"
    fields = evaluator_outcome_fields(out)
    # the earlier fail-closed attempt is counted although the last pass gave a real verdict
    assert fields["step_evaluator_failed_closed"] == 1 and fields["step_evaluator_errors"] == 2
    assert fields["verdict_modes"] == {"step": {"failed_closed": 1, "retry_no_thinking": 1},
                                       "execution": {"model": 2}}


def test_outcome_fields_are_empty_for_an_ordinary_step():
    from cmbagent_lg.deep_research.nodes import evaluator_outcome_fields
    assert evaluator_outcome_fields({"step_verdict_modes": ["model"], "execution_verdict_modes": ["model"],
                                     "step_evaluator_outcome": "verdict", "step_evaluator_errors_total": 0}) == {}
    assert evaluator_outcome_fields({}) == {}
    fields = evaluator_outcome_fields({"step_verdict_modes": ["failed_closed", "failed_closed"],
                                       "execution_verdict_modes": ["deterministic_fallback", "first_attempt"],
                                       "execution_verdict_fallbacks": 1, "step_evaluator_errors_total": 3})
    assert fields["step_evaluator_failed_closed"] == 2 and fields["execution_verdict_fallbacks"] == 1
    assert fields["verdict_modes"]["execution"] == {"deterministic_fallback": 1, "first_attempt": 1}
