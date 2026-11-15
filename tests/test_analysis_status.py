"""`logs/analysis_status.json` — the machine-readable verdict on a deep_research run.

Contract in `cmbagent_lg.persistence` (module docstring). Written at every checkpoint
(before step 1, after every step) next to `deep_research_run.json`; `status` is
`complete` ONLY when every step in the plan has a fulfilled outcome. Motivation: a
consumer (Denario's results stage) had no signal that a run HALTED and wrote the
step-summary dump as the report — 298/465 qm9-joint iterations.
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg import persistence
from cmbagent_lg.context import PlanContext
from cmbagent_lg.deep_research import nodes as dr
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.self_debug.schemas import StepVerdict


def _plan(n_eng=2, researcher=True):
    steps = [Step(sub_task=f"engineer step {i}", sub_task_agent="engineer", bullet_points=["x"])
             for i in range(1, n_eng + 1)]
    if researcher:
        steps.append(Step(sub_task="Write the Results section", sub_task_agent="researcher",
                          bullet_points=["save the report to data/results.txt"]))
    return Plan(sub_tasks=steps)


def _ok(n, attempts=1):
    return {"step_number": n, "fulfilled": True, "attempts": attempts, "escalated": False}


def _bad(n, reason, attempts=1):
    return {"step_number": n, "fulfilled": False, "attempts": attempts, "escalated": False, "reason": reason}


# ── the pure record ─────────────────────────────────────────────────────────


def test_no_outcomes_is_in_progress():
    s = persistence.analysis_status(_plan(), [])
    assert s["status"] == "in_progress"
    assert (s["n_steps"], s["n_outcomes"], s["n_fulfilled"], s["all_fulfilled"]) == (3, 0, 0, False)
    assert s["halted_at_step"] is None and s["attempts"] is None and s["reason"] is None
    assert s["final_step_agent"] == "researcher"
    assert s["updated_at"].endswith("+00:00")


def test_partial_fulfilled_is_in_progress_not_complete():
    s = persistence.analysis_status(_plan(), [_ok(1), _ok(2)])
    assert s["status"] == "in_progress" and s["all_fulfilled"] is True
    assert s["n_outcomes"] == 2 and s["n_fulfilled"] == 2


def test_all_steps_fulfilled_is_complete():
    s = persistence.analysis_status(_plan(), [_ok(1), _ok(2), _ok(3)])
    assert s["status"] == "complete" and s["all_fulfilled"] is True
    assert s["halted_at_step"] is None


def test_the_fleet_halt_is_halted_with_step_attempts_reason():
    """The qm9-joint shape: step 1 gate-halted at attempt 1, plan of 8 ending in a researcher."""
    reason = ("the required input data was never provided; the analysed data was generated "
              "by the script itself (fabricated)")
    s = persistence.analysis_status(_plan(n_eng=7), [_bad(1, reason, attempts=1)])
    assert s["status"] == "halted"
    assert s["halted_at_step"] == 1 and s["attempts"] == 1 and s["reason"] == reason
    assert s["n_steps"] == 8 and s["n_outcomes"] == 1 and s["n_fulfilled"] == 0
    assert s["final_step_agent"] == "researcher"


def test_halted_reports_the_first_unfulfilled_step():
    s = persistence.analysis_status(_plan(n_eng=4), [_ok(1), _bad(2, "boom", 3), _bad(3, "later")])
    assert s["status"] == "halted" and s["halted_at_step"] == 2 and s["attempts"] == 3
    assert s["reason"] == "boom" and s["n_fulfilled"] == 1


def test_never_complete_with_more_steps_than_outcomes_after_a_replan():
    """Adaptive planning grew the plan: the earlier 'complete' becomes in_progress."""
    assert persistence.analysis_status(_plan(n_eng=1), [_ok(1), _ok(2)])["status"] == "complete"
    assert persistence.analysis_status(_plan(n_eng=3), [_ok(1), _ok(2)])["status"] == "in_progress"


def test_malformed_outcomes_never_raise():
    s = persistence.analysis_status(_plan(), [{}, None, {"fulfilled": True}])
    assert s["status"] == "halted" and s["halted_at_step"] == 1 and s["reason"] is None
    s = persistence.analysis_status(Plan(sub_tasks=[]), [])
    assert s["status"] == "in_progress" and s["final_step_agent"] is None and s["n_steps"] == 0
    s = persistence.analysis_status(_plan(), [{"step_number": 1, "fulfilled": False, "reason": ValueError("x")}])
    assert s["reason"] == "x"


# ── the file ────────────────────────────────────────────────────────────────


def test_save_and_load_roundtrip_is_atomic(tmp_path):
    out = persistence.save_analysis_status(tmp_path, _plan(), [_ok(1)])
    assert out == tmp_path / "logs" / "analysis_status.json"
    assert not (tmp_path / "logs" / "analysis_status.json.tmp").exists()
    rec = json.loads(out.read_text())
    assert rec == persistence.load_analysis_status(tmp_path) | {"updated_at": rec["updated_at"]}
    assert rec["status"] == "in_progress"
    assert persistence.load_analysis_status(tmp_path / "nowhere") is None


def test_checkpoint_writes_both_files(tmp_path):
    dr._checkpoint(str(tmp_path), _plan(), [_ok(1), _bad(2, "x")], ["s1", "s2"])
    assert (tmp_path / "logs" / "deep_research_run.json").is_file()
    rec = json.loads((tmp_path / "logs" / "analysis_status.json").read_text())
    assert rec["status"] == "halted" and rec["halted_at_step"] == 2
    dr._checkpoint(None, _plan(), [], [])  # no work_dir → no-op, no raise


def test_status_is_written_even_when_the_summary_write_fails(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(dr, "save_deep_research_summary", _boom)
    dr._checkpoint(str(tmp_path), _plan(), [_ok(1)], ["s1"])
    assert not (tmp_path / "logs" / "deep_research_run.json").exists()
    assert json.loads((tmp_path / "logs" / "analysis_status.json").read_text())["status"] == "in_progress"


def test_a_failed_status_write_never_breaks_the_run(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(dr, "save_analysis_status", _boom)
    dr._checkpoint(str(tmp_path), _plan(), [_ok(1)], ["s1"])  # must not raise
    assert (tmp_path / "logs" / "deep_research_run.json").is_file()


# ── through run_step ────────────────────────────────────────────────────────


class _SubGraph:
    """Stands in for the self_debug / researcher sub-graphs. Records what the status file
    said WHILE the step was running (i.e. before its checkpoint)."""

    def __init__(self, work_dir, fulfilled_by_step):
        self.work_dir, self.fulfilled_by_step, self.seen = work_dir, fulfilled_by_step, []

    def invoke(self, inp, context=None):
        self.seen.append(persistence.load_analysis_status(self.work_dir))
        n = inp["step_number"]
        ok = self.fulfilled_by_step.get(n, True)
        return {
            "step_number": n, "attempts": 1 if ok else 3, "escalated": False,
            "current_step_verdict": StepVerdict(fulfilled=ok, feedback="" if ok else f"step {n} failed",
                                                unmet_requirements=[] if ok else ["thing"]),
            "execution_stdout": "42\n",
        }


def _run(tmp_path, monkeypatch, plan, fulfilled_by_step):
    sub = _SubGraph(str(tmp_path), fulfilled_by_step)
    monkeypatch.setattr(dr, "self_debug_graph", sub)
    monkeypatch.setattr(dr, "researcher_graph", sub)
    rt = types.SimpleNamespace(context=PlanContext(main_task="t", adaptive_planning=False))
    state = {"plan": plan, "work_dir": str(tmp_path), "step_index": 1,
             "step_outcomes": [], "step_summaries": []}
    statuses = []
    while state["step_index"] <= len(plan.sub_tasks):
        out = dr.run_step(state, rt)
        state.update(out)
        statuses.append(persistence.load_analysis_status(str(tmp_path)))
        if dr.after_step(state, rt) == "__end__" and not state["step_outcomes"][-1]["fulfilled"]:
            break
    return sub, statuses


def test_in_progress_is_on_disk_before_step_1_runs(tmp_path, monkeypatch):
    sub, statuses = _run(tmp_path, monkeypatch, _plan(n_eng=1), {})
    assert sub.seen[0] is not None and sub.seen[0]["status"] == "in_progress"
    assert sub.seen[0]["n_outcomes"] == 0 and sub.seen[0]["n_steps"] == 2
    assert [s["status"] for s in statuses] == ["in_progress", "complete"]
    assert statuses[-1]["n_fulfilled"] == 2 and statuses[-1]["final_step_agent"] == "researcher"


def test_a_halt_at_step_1_is_halted_never_complete(tmp_path, monkeypatch):
    sub, statuses = _run(tmp_path, monkeypatch, _plan(n_eng=7), {1: False})
    assert len(statuses) == 1
    rec = statuses[0]
    assert rec["status"] == "halted" and rec["halted_at_step"] == 1 and rec["attempts"] == 3
    assert rec["reason"] == "step 1 failed" and rec["n_steps"] == 8
    run = json.loads((tmp_path / "logs" / "deep_research_run.json").read_text())
    assert run["all_fulfilled"] is False and len(run["outcomes"]) == 1


def test_unsupported_agent_halt_is_recorded(tmp_path, monkeypatch):
    plan = Plan(sub_tasks=[Step(sub_task="x", sub_task_agent="plotter", bullet_points=[])])
    sub, statuses = _run(tmp_path, monkeypatch, plan, {})
    assert statuses[-1]["status"] == "halted" and "not supported" in statuses[-1]["reason"]
