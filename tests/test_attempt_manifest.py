"""The attempt manifest: every file an attempt wrote, wherever in the run directory, and a
check of every file path its stdout names.

The executor used to snapshot only the top level of `data/`, so `answer.json` at the root
of the run directory, `work/spectra.npz` and `data/plots/x.png` were invisible and the
step evaluator was told "(no output files were produced)" for steps whose files existed
(FLAMINGO calibration cases 4, 6, 8, 15). These tests run the real executor on small
scripts (a real subprocess, no model, no network) and pin down: files at the top level,
in a subdirectory and in nested `data/`; a stale untouched file; the engine's own
directories excluded; the walk caps; the three outcomes of a stdout-named path including
an absolute path outside the run directory; the evaluator wording; and the consumers
(image reviewer, workspace listing, infeasibility check, remote mode).

Run:
    python -m pytest -q tests/test_attempt_manifest.py
"""

import json
import os
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import attempt_manifest as am
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.prompts import step_evaluator_instructions
from cmbagent_lg.self_debug.schemas import EngineerResponse

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.delenv(REMOTE_ENV, raising=False)
    monkeypatch.delenv("CMBAGENT_LG_MANIFEST_MAX_FILES", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_MANIFEST_MAX_DEPTH", raising=False)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(sub_task="do it", *bullets):
    return Step(sub_task=sub_task, sub_task_agent="engineer", bullet_points=list(bullets) or ["x"])


def _run(work_dir, code, step=None, **kw):
    state = {
        "step": step or _step(),
        "current_code": EngineerResponse(python_code=textwrap.dedent(code), code_explanation="x"),
        "attempts": 1,
        "step_number": 1,
        "work_dir": str(work_dir),
    }
    state.update(kw)
    return sd_nodes.executor(state, _rt(PlanContext(main_task="t")))


def _paths(out):
    return [f["path"] for f in out["data_manifest"]]


def _make_old(path: Path, age_s: float = 3600):
    t = time.time() - age_s
    os.utime(path, (t, t))


# ── where the files are ──────────────────────────────────────────────────────


def test_files_at_the_top_level_in_a_subdirectory_and_in_nested_data(tmp_path):
    out = _run(tmp_path, """
        import os
        os.makedirs("work", exist_ok=True)
        os.makedirs("data/plots", exist_ok=True)
        open("answer.json", "w").write('{"a": 1}')
        open("work/spectra.npz", "wb").write(b"0" * 10)
        open("data/plots/x.png", "wb").write(b"png")
        open("data/top.csv", "w").write("a,b\\n")
        print("done")
    """)
    assert out["execution_returncode"] == 0, out["execution_stderr"]
    assert _paths(out) == ["answer.json", "data/plots/x.png", "data/top.csv",
                           "work/spectra.npz"]
    sizes = {f["path"]: f["bytes"] for f in out["data_manifest"]}
    assert sizes["work/spectra.npz"] == 10
    assert all(f["change"] == "new" for f in out["data_manifest"])
    info = out["data_manifest_info"]
    assert info["scope"] == "work_dir" and info["complete"] is True
    log = json.loads((tmp_path / "logs" / "step_1_data_manifest.json").read_text())
    assert [f["path"] for f in log["files"]] == _paths(out)
    assert log["complete"] is True and log["scope"] == "work_dir"


def test_a_stale_untouched_file_is_not_listed_and_a_rewritten_one_is(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "work").mkdir()
    stale = tmp_path / "data" / "loocv_results.parquet"
    stale.write_text("stale")
    _make_old(stale)
    old = tmp_path / "work" / "old.txt"
    old.write_text("old")
    _make_old(old)
    out = _run(tmp_path, """
        open("work/old.txt", "w").write("rewritten")
        print("ok")
    """)
    assert _paths(out) == ["work/old.txt"]
    assert out["data_manifest"][0]["change"] == "modified"


def test_engine_directories_and_caches_are_excluded(tmp_path):
    out = _run(tmp_path, """
        import os
        for d in ("codebase", "logs", "planning", "reports", "chats", "__pycache__",
                  ".cache", ".ipynb_checkpoints", ".hidden", "work/__pycache__"):
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "f.txt"), "w").write("x")
        open("langfuse_trace_id.txt", "w").write("x")
        open("work/kept.txt", "w").write("x")
        print("ok")
    """)
    assert _paths(out) == ["work/kept.txt"]


def test_symlinked_directories_are_not_entered_except_a_data_link(tmp_path):
    big = tmp_path / "elsewhere"
    big.mkdir()
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    wd = tmp_path / "run"
    wd.mkdir()
    (wd / "inputs").symlink_to(big, target_is_directory=True)
    (wd / "data").symlink_to(real_data, target_is_directory=True)
    out = _run(wd, """
        open("inputs/new.csv", "w").write("x")
        open("data/out.csv", "w").write("x")
        print("ok")
    """)
    assert _paths(out) == ["data/out.csv"]
    assert out["data_manifest_info"]["symlinked_dirs"] == ["inputs"]


# ── caps ────────────────────────────────────────────────────────────────────


def test_the_file_cap_marks_the_listing_incomplete_and_the_evaluator_is_told(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"in_{i}.txt").write_text("x")
        _make_old(tmp_path / f"in_{i}.txt")
    monkeypatch.setenv("CMBAGENT_LG_MANIFEST_MAX_FILES", "3")
    out = _run(tmp_path, """
        open("zz_answer.json", "w").write("{}")
        print("ok")
    """)
    info = out["data_manifest_info"]
    assert info["complete"] is False
    assert "3-file cap" in info["incomplete_reason"]
    text = am.render_for_evaluator(out["data_manifest"], info)
    assert "INCOMPLETE" in text and "do not conclude from its absence" in text


def test_the_depth_cap_marks_the_listing_incomplete(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("x")
    (tmp_path / "a" / "y.txt").write_text("y")
    snap = am.snapshot_tree(tmp_path, max_files=100, max_depth=2)
    assert "a/y.txt" in snap.files and "a/b/c/x.txt" not in snap.files
    assert snap.truncated and "deeper than 2" in snap.truncated


def test_a_file_past_a_truncated_baseline_is_listed_only_if_new(tmp_path):
    """With the baseline cut at the cap, a file absent from it may just have been past
    the cut: it counts only if its mtime is not older than the attempt."""
    before = am.WalkResult()
    before.truncated = "the scan stopped at the 1-file cap"
    started = time.time_ns()
    after = am.WalkResult()
    after.files = {"old.txt": (1, started - 10 * am.MTIME_SLACK_NS),
                   "new.txt": (1, started + 1)}
    assert [f["path"] for f in am.diff_snapshots(before, after, started)] == ["new.txt"]


def test_the_listing_cap_counts_what_it_omits(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "MAX_LISTED", 2)
    out = _run(tmp_path, """
        for i in range(4):
            open(f"f{i}.txt", "w").write("x")
        print("ok")
    """)
    assert len(out["data_manifest"]) == 2
    assert out["data_manifest_info"]["omitted"] == 2
    assert "and 2 more file(s)" in am.render_for_evaluator(
        out["data_manifest"], out["data_manifest_info"])


# ── stdout-named paths ──────────────────────────────────────────────────────


def test_stdout_paths_written_pre_existing_and_missing(tmp_path, tmp_path_factory):
    wd = tmp_path / "run"
    (wd / "data").mkdir(parents=True)
    stale = wd / "data" / "old.csv"
    stale.write_text("old")
    _make_old(stale)
    outside = tmp_path_factory.mktemp("outside")
    outside_old = outside / "prior.json"
    outside_old.write_text("{}")
    _make_old(outside_old)
    out_abs = outside / "answer.json"
    out = _run(wd, f"""
        open("data/new.csv", "w").write("x")
        open({str(out_abs)!r}, "w").write('{{"v": 1}}')
        print("saved data/new.csv")
        print("saved {out_abs}")
        print("reused data/old.csv")
        print("prior result in {outside_old}")
        print("figure at data/plots/missing.png")
    """)
    checks = {c["path"]: c["status"] for c in out["data_manifest_info"]["stdout_paths"]}
    assert checks == {
        "data/new.csv": "written",
        str(out_abs): "written",
        "data/old.csv": "pre_existing",
        str(outside_old): "pre_existing",
        "data/plots/missing.png": "missing",
    }
    # The outside file this attempt wrote joins the manifest by its absolute path.
    entry = [f for f in out["data_manifest"] if f.get("source") == "stdout"]
    assert [f["path"] for f in entry] == [str(out_abs)]
    assert "data/old.csv" not in _paths(out)
    text = am.render_for_evaluator(out["data_manifest"], out["data_manifest_info"])
    assert f"- {out_abs} " in text and "outside the run directory; named in stdout" in text
    assert "- data/old.csv: exists, but was NOT written or modified by this attempt" in text
    assert "- data/plots/missing.png: does NOT exist" in text


def test_stdout_token_extraction_is_conservative():
    text = ("see https://example.org/data/file.json and np.linalg.norm, scipy.optimize, "
            "3.14, e.g. this; saved 'work/x.npz', (data/y.csv), /abs/z.fits. "
            "warning in /opt/lib/site.py:12")
    assert am.stdout_path_tokens(text) == ["work/x.npz", "data/y.csv", "/abs/z.fits"]
    many = " ".join(f"f{i}.csv" for i in range(100))
    assert len(am.stdout_path_tokens(many)) == am.MAX_STDOUT_PATHS


def test_stdout_checks_never_open_a_file_or_list_a_named_directory(tmp_path, monkeypatch):
    (tmp_path / "results.zarr").mkdir()
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: opened.append(a) or real_open(*a, **k))
    checks = am.check_stdout_paths("wrote results.zarr and data/a.csv", tmp_path, tmp_path,
                                   am.WalkResult(), (), time.time_ns())
    assert opened == []
    assert [c["path"] for c in checks] == ["data/a.csv"]


# ── the evaluator prompt ────────────────────────────────────────────────────


def _eval_prompt(manifest, info, stdout="x"):
    return step_evaluator_instructions(
        PlanContext(main_task="t"), _step(), stdout=stdout, data_manifest=manifest,
        step_feedback_history=[], data_manifest_info=info)


def test_evaluator_wording_for_an_attempt_that_wrote_nothing(tmp_path):
    out = _run(tmp_path, "print('no files')\n")
    prompt = _eval_prompt(out["data_manifest"], out["data_manifest_info"])
    assert "(no output files were produced)" not in prompt
    assert ("No file in the run directory was written or modified by this attempt "
            "(checked recursively;") in prompt
    assert "stdout names no file path to check." in prompt
    assert "OUTPUT FILES WRITTEN BY THIS ATTEMPT" in prompt


def test_evaluator_lists_relative_paths_for_files_written(tmp_path):
    out = _run(tmp_path, """
        import os
        os.makedirs("work", exist_ok=True)
        open("work/spectra.npz", "wb").write(b"12345")
        print("saved work/spectra.npz")
    """)
    prompt = _eval_prompt(out["data_manifest"], out["data_manifest_info"])
    assert "written or modified by this attempt (checked recursively; paths relative" in prompt
    assert "- work/spectra.npz (5 bytes)" in prompt
    assert "- work/spectra.npz: exists; written or modified by this attempt" in prompt


def test_evaluator_wording_in_remote_mode_and_without_info():
    remote = _eval_prompt([], am.remote_info())
    assert "No file in data/ on the execution sandbox was written or modified" in remote
    assert "checked recursively" not in remote
    legacy = _eval_prompt([{"path": "data/x.csv", "bytes": 3}], None)
    assert "- data/x.csv (3 bytes)" in legacy


# ── consumers ───────────────────────────────────────────────────────────────


def test_image_reviewer_sees_nested_and_stdout_named_images(tmp_path, tmp_path_factory):
    from cmbagent_lg.vlm.images import images_from_manifest, with_images

    outside = tmp_path_factory.mktemp("figs") / "abs.png"
    out = _run(tmp_path, f"""
        import os
        os.makedirs("data/plots", exist_ok=True)
        open("data/plots/km.png", "wb").write(b"png")
        open({str(outside)!r}, "wb").write(b"png")
        print("saved {outside}")
    """)
    imgs = images_from_manifest(str(tmp_path), out["data_manifest"])
    assert sorted(map(str, imgs)) == sorted([str(tmp_path / "data/plots/km.png"), str(outside)])
    content = with_images("t", [tmp_path / "data/plots/km.png"], root=str(tmp_path))
    assert any(b.get("text") == "\nFigure — data/plots/km.png:" for b in content)


def test_infeasibility_check_counts_a_nested_written_artifact():
    from cmbagent_lg.self_debug.infeasibility_check import _written_artifacts

    names = _written_artifacts({"data_manifest": [{"path": "work/loocv.parquet", "bytes": 1}]})
    assert {"work/loocv.parquet", "loocv.parquet"} <= names


def test_remote_executor_keeps_the_guest_manifest(tmp_path, monkeypatch):
    from cmbagent_lg.self_debug import remote_exec

    class _Fake:
        @classmethod
        def from_env(cls, spec):
            return cls()

        def run_code(self, session, n, code_files, timeout):
            return {"stdout": "saved data/x.csv\n", "stderr": "", "returncode": 0,
                    "timed_out": False, "elapsed_s": 0.1,
                    "manifest": [{"path": "data/x.csv", "bytes": 4}]}

    monkeypatch.setattr(remote_exec, "RemoteExecutor", _Fake)
    monkeypatch.setenv(REMOTE_ENV, "127.0.0.1:1")
    out = _run(tmp_path, "print('x')\n")
    assert out["data_manifest"] == [{"path": "data/x.csv", "bytes": 4}]
    assert out["data_manifest_info"]["scope"] == "remote_data"
    assert out["data_manifest_info"]["stdout_paths"] == []
    prompt = _eval_prompt(out["data_manifest"], out["data_manifest_info"])
    assert "top level of data/ only" in prompt and "- data/x.csv (4 bytes)" in prompt


def test_no_work_dir_still_checks_absolute_stdout_paths(tmp_path):
    target = tmp_path / "a.json"
    state = {
        "step": _step(),
        "current_code": EngineerResponse(
            python_code=f"open({str(target)!r}, 'w').write('1')\nprint('saved {target}')\n",
            code_explanation="x"),
        "attempts": 1, "step_number": 1, "work_dir": None,
    }
    out = sd_nodes.executor(state, _rt(PlanContext(main_task="t")))
    assert out["data_manifest_info"]["scope"] == "none"
    assert [f["path"] for f in out["data_manifest"]] == [str(target)]
