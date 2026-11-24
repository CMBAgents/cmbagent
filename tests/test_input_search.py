"""The bounded search before the missing-input gate (`self_debug/input_search.py`).

A script that opened a path that does not exist, while a file of that name sits under a
declared input root, used to end the step as UNFULFILLABLE ("absent from data/"). Now the
execution evaluator searches data/ and the input roots first: a hit is an ordinary failure
and the next engineer prompt lists the locations; only a file found nowhere keeps the
terminal missing-input route.

    PYTHONPATH=. python -m pytest tests/test_input_search.py
"""
import json
import os
import types
from pathlib import Path

import pytest

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import input_search as isr
from cmbagent_lg.self_debug import nodes
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict

STEP = Step(sub_task="Load the run's output list and tabulate the redshifts", sub_task_agent="engineer",
            bullet_points=["Read output_list_ps.txt for the run."])


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _fnf_state(wd: Path, path: str, attempts: int = 1, code: str = None) -> dict:
    code = code or f"import numpy as np\nz = np.loadtxt('{path}')\nprint(z)\n"
    return {
        "step": STEP, "step_number": 1, "work_dir": str(wd), "attempts": attempts,
        "current_code": EngineerResponse(python_code=code, code_explanation="t"),
        "execution_stderr": (f"Traceback (most recent call last):\n  File \"step_1.py\", line 2\n"
                             f"FileNotFoundError: [Errno 2] No such file or directory: '{path}'\n"),
        "execution_stdout": "", "execution_returncode": 1,
    }


def _verdict():
    return ExecutionVerdict(status="failure", failure_kind="other",
                            error_summary="FileNotFoundError: no such file", fix_suggestion="fix the path")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """work_dir with an empty data/, and an input root holding <root>/<run>/output_list_ps.txt."""
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    wd = tmp_path / "work"
    (wd / "data").mkdir(parents=True)
    root = tmp_path / "products"
    for run in ("L1000N1800_HYDRO", "L1000N1800_DMO"):
        (root / run / "power_spectra").mkdir(parents=True)
        (root / run / "output_list_ps.txt").write_text("0.0\n0.5\n")
        (root / run / "power_spectra" / "ps_0000.txt").write_text("1 2\n")
    return wd, root


def _evaluate(state, ctx, monkeypatch):
    """execution_evaluator with the model call replaced by a fixed failure verdict."""
    class _S:
        def invoke(self, *a, **k):
            return _verdict()

    class _M:
        def with_structured_output(self, *a, **k):
            return _S()
    monkeypatch.setattr(nodes, "_critic", lambda ctx: _M())
    out = nodes.execution_evaluator(state, _rt(ctx))
    return {**state, **out}


def test_same_basename_under_an_input_root_is_found_and_ranked(layout):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    wrong = str(root / "L1000N1800_HYDRO" / "power_spectra" / "output_list_ps.txt")
    rec = isr.search_missing_input(wrong, wd, ctx, attempt=1)
    assert rec["n_found"] == 2
    # the run the script named comes first
    assert rec["found"][0] == str(root / "L1000N1800_HYDRO" / "output_list_ps.txt")
    assert rec["truncated"] is None and isr.search_found(rec)


def test_wrong_path_is_an_ordinary_failure_with_the_locations_in_the_next_prompt(layout, monkeypatch, capsys):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)], max_n_attempts=3)
    wrong = str(root / "L1000N1800_HYDRO" / "power_spectra" / "output_list_ps.txt")
    state = _evaluate(_fnf_state(wd, wrong), ctx, monkeypatch)
    assert state["missing_input_search"]["found"]
    assert nodes._missing_provided_input(state, ctx) is None
    assert nodes.route_after_execution_evaluator(state, _rt(ctx)) == "engineer"
    err = capsys.readouterr().err
    assert "[input_search] step 1 attempt 1" in err and "NOT a missing input" in err
    logged = json.loads((wd / "logs" / "step_1_missing_input_search_1.json").read_text())
    assert logged["found"][0].endswith("L1000N1800_HYDRO/output_list_ps.txt")
    assert state["missing_input_searches"][0]["found"] is True
    note = nodes._input_search_note(state, attempts=2)
    assert "does NOT exist" in note and str(root / "L1000N1800_HYDRO" / "output_list_ps.txt") in note
    assert nodes._input_search_note(state, attempts=3) == ""          # stale after one attempt
    block = nodes.render_retry_context(
        attempts=2, max_attempts=3, last_code="x", last_stderr="e", last_stdout="", last_returncode=1,
        last_timed_out=False, last_fix_suggestion=None, error_history=["a"], input_search_note=note)
    assert block.index("WRONG INPUT PATH") < block.index("previous attempt's code")


def test_a_file_found_nowhere_keeps_the_missing_input_route(layout, monkeypatch):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    absent = str(root / "L1000N1800_HYDRO" / "halo_catalogue_zzz.hdf5")
    state = _evaluate(_fnf_state(wd, absent), ctx, monkeypatch)
    rec = state["missing_input_search"]
    assert rec["found"] == [] and rec["close"] == []
    assert nodes.route_after_execution_evaluator(state, _rt(ctx)) == "missing_input"
    out = nodes.missing_input(state, _rt(ctx))
    assert out["unfulfillable"] is True
    assert "data/ and the declared input roots" in out["current_step_verdict"].feedback
    logged = json.loads((wd / "logs" / "step_1_missing_input.json").read_text())
    assert logged["search"]["basename"] == "halo_catalogue_zzz.hdf5"
    assert out["missing_input_record"]["missing"] == "halo_catalogue_zzz.hdf5"


def test_truncated_user_dir_is_found_by_basename(layout, monkeypatch):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    truncated = "/mnt/ceph/users/bboll/flamingo/L1000N1800_DMO/output_list_ps.txt"
    state = _evaluate(_fnf_state(wd, truncated), ctx, monkeypatch)
    assert state["missing_input_search"]["found"][0] == str(root / "L1000N1800_DMO" / "output_list_ps.txt")
    assert nodes.route_after_execution_evaluator(state, _rt(ctx)) == "engineer"


def test_close_name_in_the_existing_parent(layout):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    misspelt = str(root / "L1000N1800_DMO" / "power_spectra" / "ps_000.txt")
    rec = isr.search_missing_input(misspelt, wd, ctx, attempt=2)
    assert rec["found"] == []
    assert rec["close"] == [str(root / "L1000N1800_DMO" / "power_spectra" / "ps_0000.txt")]
    assert "close name" in isr.render_search_note(rec)


def test_without_input_roots_data_is_still_searched(tmp_path, monkeypatch):
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    wd = tmp_path / "w"
    (wd / "data" / "sub").mkdir(parents=True)
    (wd / "data" / "sub" / "cat.csv").write_text("a\n1\n")
    rec = isr.search_missing_input("data/cat.csv", wd, PlanContext(main_task="t"), attempt=1)
    assert rec["found"] == [str((wd / "data" / "sub" / "cat.csv").resolve())]


def test_symlink_loops_and_bounds(tmp_path, monkeypatch):
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    os.symlink(root, root / "a" / "loop")                  # a -> root -> a -> ...
    (root / "a" / "x.txt").write_text("1")
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    rec = isr.search_missing_input("/nowhere/x.txt", tmp_path / "w", ctx, attempt=1)
    assert rec["n_found"] == 1 and rec["truncated"] is None
    deep = root
    for i in range(5):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "y.txt").write_text("1")
    shallow = isr.search_missing_input("/nowhere/y.txt", tmp_path / "w", ctx, limits={"depth": 2})
    assert shallow["n_found"] == 0
    capped = isr.search_missing_input("/nowhere/y.txt", tmp_path / "w", ctx, limits={"entries": 3})
    assert capped["truncated"] == "entries"


def test_a_stale_search_does_not_spare_a_later_attempt(layout, monkeypatch):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    absent = str(root / "nothing_like_it.fits")
    state = _fnf_state(wd, absent, attempts=2)
    state["current_execution_verdict"] = _verdict()
    state["missing_input_search"] = {"attempt": 1, "found": ["/somewhere/else.fits"], "close": []}
    assert not isr.found_elsewhere(state)
    assert nodes._missing_provided_input(state, ctx) == "nothing_like_it.fits"


def test_no_search_for_a_package_fault_or_a_created_file(layout, monkeypatch):
    wd, root = layout
    ctx = PlanContext(main_task="t", input_roots=[str(root)])
    path = str(wd / "data" / "output_list_ps.txt")
    writes = f"open('{path}', 'w').write('1')\nimport numpy as np\nnp.loadtxt('{path}')\n"
    state = _evaluate(_fnf_state(wd, path, code=writes), ctx, monkeypatch)
    assert "missing_input_search" not in state


def test_remote_executor_skips_the_search(layout, monkeypatch):
    wd, root = layout
    monkeypatch.setenv("CMBAGENT_LG_REMOTE_EXECUTOR", "http://127.0.0.1:1")
    rec = isr.search_missing_input("/x/output_list_ps.txt", wd, PlanContext(main_task="t", input_roots=[str(root)]))
    assert rec["skipped"] == "remote executor" and not isr.search_found(rec)
