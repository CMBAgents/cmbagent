"""A code-gate failure that is a fact about the DATA is named and bounded.

`StepVerdict.infeasible_requirements` gives the GOAL gate a way to say "not possible
here". The CODE gate had none: a model that cannot be fitted on this cohort was an
anonymous `returncode 1`, retried until the budget was gone. metabric Project4 step 5
spent four of its eight attempts there, three of them on the identical lifelines
`ConvergenceError('delta contains nan value(s)')` from a clusters x INTCLUST interaction
design that is rank-deficient on 1,392 samples.

Two deterministic jobs, no model call. COUNT each numerical fault quietly, per marker;
then, once the SAME marker has come back `max_data_infeasibility_repeats` times, STATE
the conclusion (`execution_infeasible_signal`, `logs/step_N_infeasible_execution.json`)
and stop — after which `deep_research` sends the step to the adaptive replanner, since a
requirement the data cannot support is a plan problem.

Observation and conclusion are separate on purpose, and these tests pin that: three
DIFFERENT failures in a row (ConvergenceError, then a pandas `KeyError`, then an
`UnboundLocalError`) is an engineer making real progress, and a feasibility-named record
written on the first of them would be a false statement a later reader believes.

Run:
    python tests/test_data_infeasibility.py
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.data_infeasibility import classify, observe, retries_spent
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict

# The real traceback tail, from codebase/step_5_failure_4.log.
CONVERGENCE = (
    'Traceback (most recent call last):\n'
    '  File "step_5.py", line 118, in <module>\n'
    '    cph.fit(design, duration_col="OS_MONTHS", event_col="event")\n'
    '  File ".../lifelines/fitters/coxph_fitter.py", line 1572, in '
    '_newton_raphson_for_efron_model\n'
    "    raise ConvergenceError('delta contains nan value(s)')\n"
    'lifelines.exceptions.ConvergenceError: delta contains nan value(s). '
    'Convergence halted. Please see the following tips in the lifelines documentation\n'
)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(**kw):
    st = {
        "step": Step(sub_task="competing risks", sub_task_agent="engineer",
                     bullet_points=["fit the interaction model"]),
        "step_number": 5,
        "attempts": 1,
        "execution_stderr": CONVERGENCE,
        "execution_stdout": "",
        "execution_returncode": 1,
    }
    st.update(kw)
    return st


_FAILED = ExecutionVerdict(status="failure", failure_kind="other",
                           error_summary="ConvergenceError")
_OK = ExecutionVerdict(status="success")


# ── (1) the classifier ────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,label", [
    (CONVERGENCE, "non_convergence"),
    ("lifelines ConvergenceError: delta contains nan value(s)", "non_convergence"),
    ("RuntimeWarning: the model failed to converge", "non_convergence"),
    ("numpy.linalg.LinAlgError: Singular matrix", "singular_design"),
    ("statsmodels: the design matrix is rank-deficient", "singular_design"),
    ("Hessian is not positive definite", "singular_design"),
    ("PerfectSeparationError: perfect separation detected", "perfect_separation"),
    ("ValueError: too few events to fit the model", "insufficient_data"),
])
def test_data_faults_are_recognised(text, label):
    got = classify(text)
    assert got is not None and got.startswith(label), got


@pytest.mark.parametrize("text", [
    "KeyError: 'OS_MONTHS'",
    "TypeError: unsupported operand type(s) for +: 'int' and 'str'",
    "ValueError: could not convert string to float: 'NA'",
    "FileNotFoundError: [Errno 2] No such file or directory: 'data/x.csv'",
    "ModuleNotFoundError: No module named 'sksurv'",
    "[killed: TimeoutExpired after 600s]",
    "",
    None,
])
def test_ordinary_bugs_are_not_data_faults(text):
    """A false positive would end a step another attempt could have fixed."""
    assert classify(text) is None


def test_a_caught_and_printed_error_is_still_seen():
    """The engineer often try/excepts and prints, exiting 0 with empty stderr."""
    assert classify("", "caught: ConvergenceError: delta contains nan value(s)")


# ── (2) observing and counting ───────────────────────────────────────────────


def test_a_success_and_an_unrelated_failure_record_nothing():
    assert observe(_state(), _OK) == {}
    assert observe(_state(execution_stderr="KeyError: 'x'"), _FAILED) == {}


def test_one_observation_claims_nothing(tmp_path):
    """The engine must not say "the data cannot support this" the first time a fit
    fails — the next attempt often gets past it."""
    ctx = PlanContext(main_task="t", max_data_infeasibility_repeats=3)
    st = _state(work_dir=str(tmp_path))
    out = observe(st, _FAILED, ctx)
    marker, = out["numeric_fault_seen"]
    assert marker.startswith("non_convergence")
    assert out["numeric_fault_seen"][marker] == [1]
    assert "execution_infeasible_signal" not in out, "a conclusion was drawn too early"
    assert not (tmp_path / "logs" / "step_5_infeasible_execution.json").exists()


def test_the_conclusion_is_stated_once_the_fault_recurs(tmp_path):
    ctx = PlanContext(main_task="t", max_data_infeasibility_repeats=3)
    st = _state(work_dir=str(tmp_path))
    for attempt in (1, 2, 3):
        st["attempts"] = attempt
        out = observe(st, _FAILED, ctx)
        st.update(out)
    marker = out["execution_infeasible_signal"]
    assert marker.startswith("non_convergence")
    assert st["numeric_fault_seen"][marker] == [1, 2, 3]
    # The file carries "infeasible" in its NAME: the run's own record has to say what
    # the engine CONCLUDED, not just "returncode 1" three times.
    rec = json.loads(
        (tmp_path / "logs" / "step_5_infeasible_execution.json").read_text())
    assert rec["step_number"] == 5 and rec["marker"] == marker
    assert rec["seen_on_attempts"] == [1, 2, 3]
    assert "DATA" in rec["conclusion"]
    assert "delta contains nan" in rec["stderr_tail"]


def test_three_different_faults_never_reach_a_conclusion(tmp_path):
    """The `distinct` half: each attempt got past the last one's problem, so nothing is
    concluded and nothing feasibility-named is written."""
    ctx = PlanContext(main_task="t", max_data_infeasibility_repeats=3)
    st = _state(work_dir=str(tmp_path))
    for attempt, err in enumerate((
            CONVERGENCE,
            "numpy.linalg.LinAlgError: Singular matrix",
            "ValueError: too few events to fit the model"), start=1):
        st["attempts"] = attempt
        st["execution_stderr"] = err
        st.update(observe(st, _FAILED, ctx))
    assert len(st["numeric_fault_seen"]) == 3, \
        "two different numerical faults are two problems, each with its own retries"
    assert st.get("execution_infeasible_signal") is None
    assert not (tmp_path / "logs" / "step_5_infeasible_execution.json").exists()
    assert retries_spent(st, ctx) is False


def test_observe_never_raises():
    assert observe({}, _FAILED) == {} or True          # no step/stderr at all
    assert observe(_state(), object()) == {}           # verdict without .status


# ── (3) the budget, and the router ───────────────────────────────────────────


def test_the_engineer_keeps_its_repair_attempts():
    ctx = PlanContext(main_task="t", max_n_attempts=8)       # default limit: 3
    marker = "non_convergence: delta contains nan"
    assert retries_spent({"numeric_fault_seen": {marker: [1]}}, ctx) is False
    assert retries_spent({"numeric_fault_seen": {marker: [1, 2]}}, ctx) is False
    assert retries_spent({"numeric_fault_seen": {marker: [1, 2, 3]}}, ctx) is True
    # Nothing seen, or the feature off → never stops.
    assert retries_spent({}, ctx) is False
    off = PlanContext(main_task="t", stop_on_repeated_data_infeasibility=False)
    assert retries_spent({"numeric_fault_seen": {marker: [1, 2, 3, 4]}}, off) is False
    loose = PlanContext(main_task="t", max_data_infeasibility_repeats=5)
    assert retries_spent({"numeric_fault_seen": {marker: [1, 2, 3]}}, loose) is False
    # Three DIFFERENT faults are three problems, not one wall.
    assert retries_spent({"numeric_fault_seen": {"a: x": [1], "b: y": [2],
                                                 "c: z": [3]}}, ctx) is False


def test_the_router_stops_the_step_once_the_fault_repeats():
    ctx = PlanContext(main_task="t", max_n_attempts=8, enable_intent_gate=False,
                      enable_data_fixer=False, enable_escalation=False)
    marker = "non_convergence: delta contains nan"
    st = _state(current_execution_verdict=_FAILED, attempts=2,
                numeric_fault_seen={marker: [1, 2]})
    # Two observations: the engineer still gets its third go.
    assert sd_nodes.route_after_execution_evaluator(st, _rt(ctx)) == "engineer"
    st["numeric_fault_seen"] = {marker: [1, 2, 3]}
    st["execution_infeasible_signal"] = marker
    st["attempts"] = 3
    assert sd_nodes.route_after_execution_evaluator(st, _rt(ctx)) == END
    # With five attempts still in the budget — that is the saving.
    assert st["attempts"] < ctx.max_n_attempts


def test_a_missing_package_still_escalates_instead(tmp_path):
    """The recoverable hatches are checked first: a `ConvergenceError` is `other`, but
    a run that ALSO looks like a missing module must still reach escalation."""
    ctx = PlanContext(main_task="t", max_n_attempts=8, enable_escalation=True,
                      enable_intent_gate=False, enable_data_fixer=False)
    marker = "non_convergence: delta contains nan"
    st = _state(attempts=3, execution_infeasible_signal=marker,
                numeric_fault_seen={marker: [1, 2, 3]},
                current_execution_verdict=ExecutionVerdict(
                    status="failure", failure_kind="missing_module",
                    error_summary="No module named 'sksurv'"))
    assert sd_nodes.route_after_execution_evaluator(st, _rt(ctx)) == "escalation"


# ── (4) end to end through the real graph ───────────────────────────────────


def test_the_graph_stops_early_and_leaves_the_record(tmp_path, monkeypatch):
    """Three executions instead of eight, and the engine's own log says why."""
    from cmbagent_lg.self_debug.graph import graph

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)

    script = ("import sys\n"
              "print('fitting the interaction model')\n"
              "sys.stderr.write(%r)\n"
              "sys.exit(1)\n" % CONVERGENCE)
    runs = []

    class _Proposer:
        def invoke(self, msgs, config=None):
            runs.append(msgs[0].content)
            return types.SimpleNamespace(text=script)

    class _Formatter:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

    class _Critic:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _msgs, config=None):
            return ExecutionVerdict(status="failure", failure_kind="other",
                                    error_summary="ConvergenceError",
                                    fix_suggestion="try a penalizer")

    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())

    ctx = PlanContext(main_task="t", max_n_attempts=8, enable_preflight=False,
                      enable_intent_gate=False, enable_escalation=False,
                      vlm_enabled=False)
    step = Step(sub_task="competing risks", sub_task_agent="engineer",
                bullet_points=["fit the interaction model"])
    out = graph.invoke({"step": step, "step_number": 5, "work_dir": str(tmp_path)},
                       context=ctx)

    assert len(runs) == 3, f"expected 3 attempts, not {len(runs)} (cap was 8)"
    assert out["attempts"] == 3
    assert out["execution_infeasible_signal"].startswith("non_convergence")
    rec = json.loads(
        (tmp_path / "logs" / "step_5_infeasible_execution.json").read_text())
    assert rec["seen_on_attempts"] == [1, 2, 3]
    assert rec["sub_task"] == "competing risks"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
