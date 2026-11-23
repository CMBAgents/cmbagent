"""Declared-output post-condition: a clean run missing a file the step declared is
bounced from the filesystem, not from an evaluator.

Nothing used to check, once the subprocess returned, that the artifacts the sub-task
named by path were actually written. A `data/cohort.parquet` that never appeared was
discovered by `step_evaluator` — i.e. after the execution verdict AND the step verdict
had both been paid for — and the engineer then heard it from a model instead of from
`os.path.isfile`.

The node sits on the executor → execution_evaluator edge and rewrites a clean run that
lacks a declared output into a failure (rc 1, the missing path named on stderr), so the
existing retry machinery — `error_history`, the demotion of `step_{N}.py`, the attempt
budget, the safety layers in `route_after_execution_evaluator` — proceeds unchanged and
bounds the bounce for free.

Presence is judged from the attempt's `data_manifest` (the executor's recursive
baseline diff of the run directory),
NOT from `Path.exists()`. metabric Project3/Iteration1 step 5 is why: a stale
`data/loocv_results.parquet` from an earlier run was sitting in `data/`, so an existence
check would have waved through all eight attempts that never wrote it, and a later step
would have read the wrong numbers out of the stale file.

Conservative by design, and the five guards are what these tests pin down: only
SAVE-shaped declarations count (an absent INPUT is the `safe_loading` layer's business,
never this one's), only unambiguous `data/...` paths, only after a run that exited 0,
never under a remote executor or without a manifest, and a path the manifest could not
see (a walk cap was hit) counts only when its mtime is not older than the attempt.

Run:
    python tests/test_declared_outputs.py
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.output_postcondition import (
    POSTCONDITION_MARKER,
    declared_output_paths,
    missing_declared_outputs,
    output_postcondition,
)

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(sub_task, *bullets):
    return Step(sub_task=sub_task, sub_task_agent="engineer", bullet_points=list(bullets))


def _clean_state(step, work_dir, **kw):
    """What the executor leaves behind after a run that exited 0."""
    st = {
        "step": step,
        "work_dir": str(work_dir) if work_dir else None,
        "step_number": 1,
        "attempts": 1,
        "execution_returncode": 0,
        "execution_timed_out": False,
        "execution_stdout": "done\n",
        "execution_stderr": "",
        "data_manifest": [],
    }
    st.update(kw)
    return st


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.delenv(REMOTE_ENV, raising=False)


def _assert_pass_through(out):
    """A pass: the key is written (so a bounce never leaks) and nothing the executor
    produced is touched. `node_elapsed_s` comes from @timed_node and is ignored."""
    assert out["output_postcondition_rejected"] is None, out
    assert not [k for k in out if k.startswith("execution_")], out


# ── (1) what counts as a declared OUTPUT ──────────────────────────────────────


@pytest.mark.parametrize("step,expected", [
    # The ordinary shapes.
    (_step("clean the cohort", "save the result to data/cohort.parquet"),
     ["data/cohort.parquet"]),
    (_step("write data/summary.csv with one row per patient"), ["data/summary.csv"]),
    (_step("fit the model", "store the coefficients in `data/coefs.json`",
           "produce data/km_curve.png"),
     ["data/coefs.json", "data/km_curve.png"]),
    (_step("export the table", "export to ./data/table.csv"), ["data/table.csv"]),
    (_step("plot it", "savefig to data/plots/km.png"), ["data/plots/km.png"]),
    # An INPUT is not an output: the nearest preceding verb reads it.
    (_step("summarise the cohort", "load data/cohort.parquet and print its shape"), []),
    (_step("fit a model", "read the provided data/raw.csv"), []),
    (_step("use the file in data/cohort.csv"), []),
    # Both in one bullet: only the saved one is a declaration.
    (_step("clean it", "load data/raw.csv, then write the cleaned frame to data/clean.csv"),
     ["data/clean.csv"]),
    # A path with no verb at all commits to nothing.
    (_step("have a look at data/cohort.csv"), []),
    # Placeholders and globs never parse.
    (_step("save each fold to data/{fold}.csv"), []),
    (_step("save the figures to data/*.png"), []),
    (_step("save the results under data/"), []),
    # Paths outside data/ are out of scope (the check only owns the data directory).
    (_step("save the model to models/model.pkl"), []),
    # A directory that merely ENDS in data/ must not match.
    (_step("save it to raw_data/x.csv"), []),
    # De-duplicated, order preserved.
    (_step("save data/a.csv", "also save data/a.csv and data/b.csv"),
     ["data/a.csv", "data/b.csv"]),
    (_step(""), []),
])
def test_declared_output_paths(step, expected):
    assert declared_output_paths(step) == expected


# ── (2) presence is decided on disk, with the manifest as a fallback ──────────


def test_missing_declared_outputs(tmp_path):
    step = _step("clean it", "save the cleaned cohort to data/cohort.parquet")
    # Written by THIS attempt → present. By path, or by basename alone.
    assert missing_declared_outputs(
        step, str(tmp_path), [{"path": "data/cohort.parquet", "bytes": 3}]) == []
    assert missing_declared_outputs(
        step, str(tmp_path), [{"path": "cohort.parquet", "bytes": 3}]) == []
    # Not in the manifest → missing, even with the file sitting on disk.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cohort.parquet").write_text("stale")
    assert missing_declared_outputs(step, str(tmp_path), []) == ["data/cohort.parquet"]
    # No workspace, or no manifest to read → never bounce blind.
    assert missing_declared_outputs(step, None, []) == []
    assert missing_declared_outputs(step, str(tmp_path), None) == []
    # No declaration → no-op, whatever is or is not in the manifest.
    assert missing_declared_outputs(_step("print the mean"), str(tmp_path), []) == []


def test_a_stale_file_from_an_earlier_run_does_not_count(tmp_path):
    """The metabric shape (Project3/Iteration1 step 5): the declared parquet IS on
    disk, left by an earlier run, and the attempt's manifest is empty because this
    attempt wrote nothing. An existence check passes it; the manifest does not."""
    ctx = PlanContext(main_task="t")
    step = _step("assess stability",
                 "Save fold-specific hazard ratios, confidence intervals, and "
                 "meta-analytic summary statistics to data/loocv_results.parquet.")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "loocv_results.parquet").write_text("stale from run 1")
    assert declared_output_paths(step) == ["data/loocv_results.parquet"]
    out = output_postcondition(_clean_state(step, tmp_path, data_manifest=[]), _rt(ctx))
    assert out["output_postcondition_rejected"] == "missing_output"
    assert "data/loocv_results.parquet" in out["execution_stderr"]
    assert "stale" in out["execution_stderr"], \
        "the message must say a leftover copy does not count"
    # The same attempt, having actually written it, passes.
    _assert_pass_through(output_postcondition(
        _clean_state(step, tmp_path,
                     data_manifest=[{"path": "data/loocv_results.parquet", "bytes": 9}]),
        _rt(ctx)))


def test_a_nested_declared_path_is_judged_from_the_manifest(tmp_path):
    """The manifest is recursive, so a nested path has per-attempt provenance like any
    other: a stale copy on disk does not pass (the old existence fallback let it), and
    the attempt that wrote it passes."""
    ctx = PlanContext(main_task="t")
    step = _step("plot it", "save the figure to data/plots/km.png")
    (tmp_path / "data" / "plots").mkdir(parents=True)
    (tmp_path / "data" / "plots" / "km.png").write_text("stale")
    out = output_postcondition(_clean_state(step, tmp_path, data_manifest=[]), _rt(ctx))
    assert out["output_postcondition_rejected"] == "missing_output"
    _assert_pass_through(output_postcondition(
        _clean_state(step, tmp_path,
                     data_manifest=[{"path": "data/plots/km.png", "bytes": 5}]),
        _rt(ctx)))


def test_an_incomplete_manifest_falls_back_to_mtime_not_existence(tmp_path, monkeypatch):
    """When the walk hit a cap the listing may omit a file the attempt wrote: such a
    file counts if it changed (mtime or ctime) after the attempt started; a stale one
    still does not. `os.utime` sets the inode's ctime to now, so the slack is removed
    and the attempt starts a moment after the setup."""
    import time

    from cmbagent_lg.self_debug import attempt_manifest as am

    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
    ctx = PlanContext(main_task="t")
    step = _step("plot it", "save the figure to data/plots/km.png")
    target = tmp_path / "data" / "plots" / "km.png"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    old = time.time() - 3600
    os.utime(target, (old, old))
    time.sleep(0.05)
    info = {"scope": "work_dir", "complete": False,
            "incomplete_reason": "the scan stopped at the 5-file cap",
            "started_ns": time.time_ns(), "stdout_paths": [], "symlinked_dirs": []}
    out = output_postcondition(
        _clean_state(step, tmp_path, data_manifest=[], data_manifest_info=info), _rt(ctx))
    assert out["output_postcondition_rejected"] == "missing_output"
    target.write_text("fresh")  # mtime now >= started_ns
    _assert_pass_through(output_postcondition(
        _clean_state(step, tmp_path, data_manifest=[], data_manifest_info=info), _rt(ctx)))


# ── (3) the node ──────────────────────────────────────────────────────────────


def test_clean_run_without_the_declared_file_is_bounced(tmp_path):
    ctx = PlanContext(main_task="t")
    step = _step("clean the cohort", "save the cleaned cohort to data/cohort.parquet")
    st = _clean_state(step, tmp_path, execution_stderr="some warning\n")
    out = output_postcondition(st, _rt(ctx))

    assert out["output_postcondition_rejected"] == "missing_output"
    assert out["execution_returncode"] == 1, "must read as a failed attempt downstream"
    err = out["execution_stderr"]
    assert "data/cohort.parquet" in err, err          # names the missing path
    assert "some warning" in err, "the real stderr must survive"
    assert POSTCONDITION_MARKER in err
    # stdout is never touched: the script really did print what it printed.
    assert "execution_stdout" not in out


def test_the_attempt_having_written_it_is_a_no_op(tmp_path):
    ctx = PlanContext(main_task="t")
    step = _step("clean the cohort", "save the cleaned cohort to data/cohort.parquet")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cohort.parquet").write_text("x")
    st = _clean_state(step, tmp_path,
                      data_manifest=[{"path": "data/cohort.parquet", "bytes": 1}])
    _assert_pass_through(output_postcondition(st, _rt(ctx)))


def test_a_missing_input_is_not_this_checks_business(tmp_path):
    """Bouncing an absent INPUT here would pressure the engineer into inventing one —
    that is the `safe_loading` layer's terminal verdict, not a retry hint."""
    ctx = PlanContext(main_task="t")
    step = _step("summarise the cohort", "load data/cohort.parquet and print its shape")
    _assert_pass_through(output_postcondition(_clean_state(step, tmp_path), _rt(ctx)))


def test_a_crashed_run_is_left_alone(tmp_path):
    """After a traceback the missing file is already explained; a second reason on top
    of it would only muddy the retry."""
    ctx = PlanContext(main_task="t")
    step = _step("clean it", "save the result to data/cohort.parquet")
    st = _clean_state(step, tmp_path, execution_returncode=1,
                      execution_stderr="ValueError: boom\n")
    _assert_pass_through(output_postcondition(st, _rt(ctx)))
    st = _clean_state(step, tmp_path, execution_returncode=124, execution_timed_out=True)
    _assert_pass_through(output_postcondition(st, _rt(ctx)))


def test_flag_off_and_remote_mode_disable_the_check(tmp_path, monkeypatch):
    step = _step("clean it", "save the result to data/cohort.parquet")
    off = PlanContext(main_task="t", check_declared_outputs=False)
    _assert_pass_through(output_postcondition(_clean_state(step, tmp_path), _rt(off)))
    # Remote: the data lives guest-side, so engine-side absence proves nothing.
    monkeypatch.setenv(REMOTE_ENV, "host:1234")
    ctx = PlanContext(main_task="t")
    _assert_pass_through(output_postcondition(_clean_state(step, tmp_path), _rt(ctx)))


def test_a_rejection_never_leaks_into_the_next_attempt(tmp_path):
    """The node writes the key on EVERY pass, so attempt N+1 starts clean."""
    ctx = PlanContext(main_task="t")
    step = _step("clean it", "save the result to data/cohort.parquet")
    st = _clean_state(step, tmp_path)
    st.update(output_postcondition(st, _rt(ctx)))
    assert st["output_postcondition_rejected"] == "missing_output"
    st.update(_clean_state(step, tmp_path, attempts=2,
                           data_manifest=[{"path": "data/cohort.parquet", "bytes": 1}]))
    st.update(output_postcondition(st, _rt(ctx)))
    assert st["output_postcondition_rejected"] is None


def test_the_check_never_raises(tmp_path):
    """A malformed step must not break the graph."""
    ctx = PlanContext(main_task="t")
    st = _clean_state(object(), tmp_path)          # no sub_task / bullet_points at all
    _assert_pass_through(output_postcondition(st, _rt(ctx)))


# ── (4) wiring: the node really is on the executor → code-gate edge ───────────


def test_node_is_wired_between_executor_and_execution_evaluator():
    from cmbagent_lg.self_debug.graph import graph

    nodes = graph.get_graph().nodes
    assert "output_postcondition" in nodes
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("executor", "output_postcondition") in edges
    assert ("output_postcondition", "execution_evaluator") in edges
    # The bounce edge: back to the engineer without passing the code gate.
    assert ("output_postcondition", "engineer") in edges
    assert ("executor", "execution_evaluator") not in edges


def test_the_router_bounces_to_the_engineer_and_respects_the_cap():
    """The bounce must not outlive the attempt budget, and must not divert into the
    missing-INPUT layers on the code gate's path."""
    from cmbagent_lg.self_debug.output_postcondition import (
        route_after_output_postcondition,
    )
    from langgraph.graph import END

    ctx = PlanContext(main_task="t", max_n_attempts=3)
    assert route_after_output_postcondition(
        {"output_postcondition_rejected": "missing_output", "attempts": 1},
        _rt(ctx)) == "engineer"
    assert route_after_output_postcondition(
        {"output_postcondition_rejected": "missing_output", "attempts": 3},
        _rt(ctx)) == END
    # A pass-through continues to the code gate exactly as before.
    assert route_after_output_postcondition(
        {"output_postcondition_rejected": None, "attempts": 1},
        _rt(ctx)) == "execution_evaluator"


def test_the_node_writes_the_verdict_the_code_gate_would_have(tmp_path):
    """It is a `stat()` call, so no model is asked to confirm it. `failure_kind` must
    stay `other`, or `escalation` would try to install a package for it."""
    ctx = PlanContext(main_task="t")
    step = _step("clean it", "save the result to data/cohort.parquet")
    codebase = tmp_path / "codebase"
    codebase.mkdir(parents=True)
    (codebase / "step_1.py").write_text("print(1)\n")
    (codebase / "step_1.log").write_text("# returncode=0\n")
    out = output_postcondition(_clean_state(step, tmp_path), _rt(ctx))

    verdict = out["current_execution_verdict"]
    assert verdict.status == "failure" and verdict.failure_kind == "other"
    assert "data/cohort.parquet" in verdict.error_summary
    assert "data/cohort.parquet" in verdict.fix_suggestion
    assert len(out["error_history"]) == 1
    # The canonical names are freed for the next attempt, artifacts kept.
    assert (codebase / "step_1_failure_1.py").exists()
    assert not (codebase / "step_1.py").exists()


# ── (5) end to end: the bounce really drives a retry ─────────────────────────


FORGETS_TO_SAVE = """import pandas as pd

if __name__ == "__main__":
    df = pd.DataFrame({"a": [1, 2, 3]})
    print("rows:", len(df))
"""

SAVES = """import os
import pandas as pd

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame({"a": [1, 2, 3]})
    df.to_csv("data/cohort.csv", index=False)
    print("rows:", len(df), "saved data/cohort.csv")
"""


def test_the_graph_retries_and_the_second_attempt_passes(tmp_path, monkeypatch):
    """The whole self_debug graph: attempt 1 runs cleanly but writes nothing, the
    post-condition turns that into a failed attempt with NO model call of any kind, and
    the engineer is asked again with the missing path in its retry block."""
    import subprocess
    import types as _types

    from cmbagent_lg.self_debug import nodes as sd_nodes
    from cmbagent_lg.self_debug.graph import graph
    from cmbagent_lg.self_debug.schemas import (
        EngineerResponse,
        ExecutionVerdict,
        StepVerdict,
    )

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv(REMOTE_ENV, raising=False)

    prompts = []
    codes = [FORGETS_TO_SAVE, SAVES]

    class _Proposer:
        def invoke(self, msgs, config=None):
            prompts.append(msgs[0].content)
            return _types.SimpleNamespace(
                text=codes[min(len(prompts) - 1, len(codes) - 1)])

    class _Formatter:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

    class _Critic:
        def __init__(self):
            self.schema = None
            self.outcomes = []            # one per execution_evaluator call
            self.step_verdict_calls = 0

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, msgs, config=None):
            if self.schema is ExecutionVerdict:
                self.outcomes.append(
                    msgs[0].content.split("----- EXECUTION OUTCOME", 1)[-1])
                return ExecutionVerdict(status="success")
            self.step_verdict_calls += 1
            return StepVerdict(fulfilled=True)

    critic = _Critic()
    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: critic)

    popen_calls = []
    real_popen = subprocess.Popen

    def _counting_popen(*a, **k):
        popen_calls.append(a[0])
        return real_popen(*a, **k)

    monkeypatch.setattr(sd_nodes.subprocess, "Popen", _counting_popen)

    step = _step("build the cohort", "save the cohort to data/cohort.csv")
    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False,
                      vlm_enabled=False, max_n_attempts=3)
    out = graph.invoke({"step": step, "step_number": 1, "work_dir": str(tmp_path)},
                       context=ctx)

    assert len(popen_calls) == 2, f"expected two executions, got {len(popen_calls)}"
    # The bounce cost NO model call: the code gate ran once, for attempt 2 only, and
    # never saw the post-condition's marker.
    assert len(critic.outcomes) == 1, \
        f"the bounced attempt spent {len(critic.outcomes) - 1} extra evaluator call(s)"
    assert POSTCONDITION_MARKER not in critic.outcomes[0]
    assert critic.step_verdict_calls == 1, "only the passing attempt is goal-judged"
    # The retry prompt carried the missing path.
    assert len(prompts) == 2 and "data/cohort.csv" in prompts[1]
    # The node did the code gate's bookkeeping itself: one error-history entry naming
    # the file, the first attempt demoted, the second attempt canonical, step passed.
    assert len(out["error_history"]) == 1
    assert "data/cohort.csv" in out["error_history"][0]
    assert out["output_postcondition_rejected"] is None, "a bounce leaked past its attempt"
    assert out["current_step_verdict"].fulfilled
    codebase = tmp_path / "codebase"
    assert (codebase / "step_1_failure_1.py").read_text() == FORGETS_TO_SAVE
    assert (codebase / "step_1.py").read_text() == SAVES
    assert (tmp_path / "data" / "cohort.csv").is_file()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
