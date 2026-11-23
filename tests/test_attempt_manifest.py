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
    monkeypatch.delenv("CMBAGENT_LG_MANIFEST_MAX_DIR_ENTRIES", raising=False)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(sub_task="do it", *bullets):
    return Step(sub_task=sub_task, sub_task_agent="engineer", bullet_points=list(bullets) or ["x"])


def _run(work_dir, code, step=None, ctx=None, **kw):
    state = {
        "step": step or _step(),
        "current_code": EngineerResponse(python_code=textwrap.dedent(code), code_explanation="x"),
        "attempts": 1,
        "step_number": 1,
        "work_dir": str(work_dir),
    }
    state.update(kw)
    return sd_nodes.executor(state, _rt(ctx or PlanContext(main_task="t")))


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
    old_ns = started - 10 * am.MTIME_SLACK_NS
    after.files = {"old.txt": (1, old_ns, old_ns, 1),
                   "new.txt": (1, started + 1, started + 1, 2)}
    assert [f["path"] for f in am.diff_snapshots(before, after, started)] == ["new.txt"]


def test_the_render_cap_shortens_only_the_prompt_not_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "MAX_RENDERED", 2)
    out = _run(tmp_path, """
        import os
        os.makedirs("work", exist_ok=True)
        for i in range(4):
            open(f"work/f{i}.txt", "w").write("x")
        print("ok")
    """)
    assert len(out["data_manifest"]) == 4
    assert out["data_manifest_info"]["omitted"] == 0
    text = am.render_for_evaluator(out["data_manifest"], out["data_manifest_info"])
    assert "- work/f0.txt (1 bytes)" in text and "- work/f3.txt" not in text
    assert "- work/: 2 more file(s) written by this attempt, 2 bytes" in text


# ── stdout-named paths ──────────────────────────────────────────────────────


def test_stdout_paths_written_pre_existing_and_missing(tmp_path, tmp_path_factory, monkeypatch):
    # `os.utime` sets ctime to now; without slack, and with the attempt starting a moment
    # after the setup, the backdated outside file reads as pre-existing.
    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
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
    time.sleep(0.05)
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


def test_infeasibility_check_counts_a_nested_written_artifact(tmp_path):
    """From a real executor run, so the test fails on the old top-level-only scan."""
    from cmbagent_lg.self_debug.infeasibility_check import _written_artifacts

    out = _run(tmp_path, """
        import os
        os.makedirs("data/plots", exist_ok=True)
        open("data/plots/km.png", "wb").write(b"png")
        print("ok")
    """)
    names = _written_artifacts(out)
    assert {"data/plots/km.png", "km.png"} <= names


def test_workspace_listing_includes_other_files_and_keeps_step_logs(tmp_path, monkeypatch):
    from cmbagent_lg.deep_research import nodes as dr_nodes

    (tmp_path / "codebase").mkdir()
    (tmp_path / "codebase" / "step_1.log").write_text("x")
    (tmp_path / "data" / "plots").mkdir(parents=True)
    (tmp_path / "data" / "top.csv").write_text("x")
    (tmp_path / "data" / "plots" / "km.png").write_text("x")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "spectra.npz").write_text("x")
    (tmp_path / "answer.json").write_text("{}")
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "final_plan.json").write_text("{}")
    text = dr_nodes._build_workspace_manifest(str(tmp_path))
    assert "- codebase/step_1.log" in text
    assert "- data/top.csv" in text
    other = text.split("**Other files in the run directory:**", 1)[1]
    assert "- answer.json" in other and "- work/spectra.npz" in other
    assert "- data/plots/km.png" in other
    assert "data/top.csv" not in other and "planning/" not in other
    monkeypatch.setattr(dr_nodes, "_WORKSPACE_OTHER_MAX", 1)
    cut = dr_nodes._build_workspace_manifest(str(tmp_path))
    assert "- ... and 2 more, not listed" in cut and "- codebase/step_1.log" in cut


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


# ── review fixes ────────────────────────────────────────────────────────────


def _postcondition(step, work_dir, out):
    from cmbagent_lg.self_debug.output_postcondition import output_postcondition

    state = {"step": step, "work_dir": str(work_dir), "step_number": 1, "attempts": 1,
             "execution_returncode": 0, "execution_timed_out": False,
             "execution_stdout": out["execution_stdout"], "execution_stderr": "",
             "data_manifest": out["data_manifest"],
             "data_manifest_info": out["data_manifest_info"]}
    return output_postcondition(state, _rt(PlanContext(main_task="t")))


def test_many_written_files_never_hide_a_declared_output(tmp_path):
    """250 chunk files plus the declared summary: the manifest keeps every entry, the
    postcondition passes, and the evaluator is shown the declared file by name."""
    step = _step("summarise", "save the summary table to data/summary.csv")
    out = _run(tmp_path, """
        import os
        os.makedirs("data/chunks", exist_ok=True)
        for i in range(250):
            open(f"data/chunks/c{i:03d}.csv", "w").write("x")
        open("data/summary.csv", "w").write("a,b")
        print("done")
    """, step=step)
    assert len(out["data_manifest"]) == 251
    assert "data/summary.csv" in _paths(out)
    assert _postcondition(step, tmp_path, out)["output_postcondition_rejected"] is None
    prompt = step_evaluator_instructions(
        PlanContext(main_task="t"), step, stdout="done", data_manifest=out["data_manifest"],
        step_feedback_history=[], data_manifest_info=out["data_manifest_info"])
    assert "- data/summary.csv (3 bytes)" in prompt
    assert "- data/chunks/: " in prompt and "more file(s) written by this attempt" in prompt
    assert prompt.count("- data/chunks/c") <= am.MAX_RENDERED


def test_a_legacy_cut_manifest_is_not_treated_as_whole():
    assert am.listing_is_whole({"complete": True, "omitted": 3}) is False
    assert am.listing_is_whole({"complete": True, "omitted": 0}) is True


def test_a_bare_name_or_a_path_printed_after_chdir_resolves_by_suffix(tmp_path):
    out = _run(tmp_path, """
        import os
        os.makedirs("data", exist_ok=True)
        open("data/spectrum.png", "wb").write(b"png")
        os.makedirs("work/out", exist_ok=True)
        os.chdir("work")
        open("out/x.npz", "wb").write(b"1")
        print("Saved figure spectrum.png")
        print("saved out/x.npz")
        print("see notes.txt")
        print("and gone/y.csv")
    """)
    checks = {c["path"]: c for c in out["data_manifest_info"]["stdout_paths"]}
    assert checks["spectrum.png"]["status"] == "written"
    assert checks["spectrum.png"]["rel"] == "data/spectrum.png"
    assert checks["out/x.npz"]["status"] == "written"
    assert checks["out/x.npz"]["rel"] == "work/out/x.npz"
    # A bare name found nowhere is not claimed to be absent; a path with a / is.
    assert checks["notes.txt"]["status"] == "not_found"
    assert checks["gone/y.csv"]["status"] == "missing"
    text = am.render_for_evaluator(out["data_manifest"], out["data_manifest_info"])
    assert "- spectrum.png: exists; written or modified by this attempt (found as " \
           "data/spectrum.png)" in text
    assert "- notes.txt: does NOT exist" not in text
    assert "- gone/y.csv: does NOT exist (resolved against the script's working " \
           "directory)" in text
    # Each file is listed once, by its run-relative path.
    assert sorted(_paths(out)) == ["data/spectrum.png", "work/out/x.npz"]


def test_a_bare_name_that_exists_but_was_not_written_is_pre_existing(tmp_path):
    (tmp_path / "data").mkdir()
    inp = tmp_path / "data" / "cohort.csv"
    inp.write_text("a")
    _make_old(inp)
    out = _run(tmp_path, "print('loaded cohort.csv')\n")
    (c,) = out["data_manifest_info"]["stdout_paths"]
    assert c["status"] == "pre_existing" and c["rel"] == "data/cohort.csv"


def test_attribute_names_with_file_extensions_are_never_reported_absent(tmp_path):
    (tmp_path / "run.log").write_text("x")
    stdout = ("np.log of x; model.fit(X) done; self.out; np.mat; ROOT.root; "
              "from astropy.io.fits import open; norm.cdf; ruamel.yaml; np.save(x); "
              "wrote run.log")
    checks = am.check_stdout_paths(stdout, tmp_path, tmp_path,
                                   am.snapshot_tree(tmp_path), (), time.time_ns())
    assert [c["path"] for c in checks] == ["run.log"]
    assert "model.fit" not in am.stdout_path_tokens(stdout)
    assert "np.save" not in " ".join(am.stdout_path_tokens(stdout))


def test_a_path_printed_through_an_alias_of_the_run_dir_is_listed_once(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    out = _run(alias, f"""
        open("answer.json", "w").write("{{}}")
        print("saved {real / 'answer.json'}")
    """)
    assert _paths(out) == ["answer.json"]
    (c,) = out["data_manifest_info"]["stdout_paths"]
    assert c["status"] == "written" and c["rel"] == "answer.json" and c["in_work_dir"]
    text = am.render_for_evaluator(out["data_manifest"], out["data_manifest_info"])
    assert "outside the run directory" not in text


def test_a_path_printed_through_a_symlinked_data_dir_maps_to_data(tmp_path):
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    wd = tmp_path / "run"
    wd.mkdir()
    (wd / "data").symlink_to(real_data, target_is_directory=True)
    out = _run(wd, f"""
        open("data/out.csv", "w").write("x")
        print("saved {real_data / 'out.csv'}")
    """)
    assert _paths(out) == ["data/out.csv"]
    (c,) = out["data_manifest_info"]["stdout_paths"]
    assert c["rel"] == "data/out.csv" and c["status"] == "written"


def test_mtime_preserving_writes_are_seen(tmp_path, tmp_path_factory, monkeypatch):
    """copy2 keeps the source's mtime; ctime (which userland cannot set) still moves."""
    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
    src_dir = tmp_path_factory.mktemp("src")
    src = src_dir / "src.txt"
    src.write_text("xx")
    (tmp_path / "work").mkdir()
    same = tmp_path / "work" / "same.txt"
    same.write_text("yy")
    t = time.time() - 3600
    os.utime(src, (t, t))
    os.utime(same, (t, t))
    outside = tmp_path_factory.mktemp("dest") / "copied.txt"
    time.sleep(0.05)
    out = _run(tmp_path, f"""
        import shutil
        shutil.copy2({str(src)!r}, "work/same.txt")
        shutil.copy2({str(src)!r}, {str(outside)!r})
        print("copied to {outside}")
    """)
    assert out["execution_returncode"] == 0, out["execution_stderr"]
    by_path = {f["path"]: f for f in out["data_manifest"]}
    assert by_path["work/same.txt"]["change"] == "modified"
    (c,) = out["data_manifest_info"]["stdout_paths"]
    assert c["status"] == "written"
    assert str(outside) in by_path


def test_attempt_start_comes_from_the_filesystem_and_leaves_no_sentinel(tmp_path):
    before = time.time_ns()
    started = am.attempt_start_ns(tmp_path)
    assert started <= time.time_ns()
    assert started >= before - 5 * 10**9
    assert list(tmp_path.iterdir()) == []
    assert am.attempt_start_ns(None) >= before


def test_a_huge_directory_is_summarised_and_the_rest_still_scanned(tmp_path, monkeypatch):
    monkeypatch.setenv("CMBAGENT_LG_MANIFEST_MAX_DIR_ENTRIES", "5")
    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
    chunks = tmp_path / "data" / "chunks"
    chunks.mkdir(parents=True)
    for i in range(10):
        (chunks / f"c{i}.csv").write_text("x")
        _make_old(chunks / f"c{i}.csv")
    (tmp_path / "zz").mkdir()
    time.sleep(0.05)
    step = _step("chunk", "write the new chunk to data/chunks/c_new.csv")
    out = _run(tmp_path, """
        open("data/chunks/c_new.csv", "w").write("x")
        open("zz/after.txt", "w").write("x")
        print("ok")
    """, step=step)
    info = out["data_manifest_info"]
    assert info["partial_dirs"] == ["data/chunks"] and info["complete"] is True
    assert _paths(out) == ["zz/after.txt"]
    # Unseen by the walk, written by the attempt: the change-time fallback passes it.
    assert _postcondition(step, tmp_path, out)["output_postcondition_rejected"] is None
    # A stale file in the same directory does not pass.
    stale_step = _step("chunk", "write the chunk to data/chunks/c0.csv")
    assert _postcondition(stale_step, tmp_path, out)["output_postcondition_rejected"] \
        == "missing_output"
    text = am.render_for_evaluator(out["data_manifest"], info)
    assert "too large to list file by file" in text and "data/chunks/" in text


def test_a_directory_that_shrank_below_the_cap_does_not_list_stale_files(tmp_path):
    before = am.WalkResult()
    before.partial_dirs = ["big"]
    started = time.time_ns()
    old = started - 10 * am.MTIME_SLACK_NS
    after = am.WalkResult()
    after.files = {"big/old.txt": (1, old, old, 1), "big/new.txt": (1, started, started, 2)}
    assert [f["path"] for f in am.diff_snapshots(before, after, started)] == ["big/new.txt"]


def test_the_stdout_check_cap_keeps_saved_and_last_paths():
    lines = [f"reading /in/file_{i}.csv" for i in range(30)] + ["saved /out/answer.json"]
    toks = am.stdout_path_tokens("\n".join(lines))
    assert len(toks) == am.MAX_STDOUT_PATHS and toks[-1] == "/out/answer.json"
    assert "/in/file_29.csv" in toks and "/in/file_0.csv" not in toks
    # A save-verb path early on survives many later ones.
    lines = ["wrote /out/first.json"] + [f"see /in/f{i}.csv" for i in range(40)]
    assert "/out/first.json" in am.stdout_path_tokens("\n".join(lines))


def test_many_symlinked_dirs_are_all_kept_for_the_postcondition(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    wd = tmp_path / "run"
    (wd / "data").mkdir(parents=True)
    for i in range(25):
        (wd / "data" / f"link{i:02d}").symlink_to(target, target_is_directory=True)
    snap = am.snapshot_tree(wd)
    assert len(snap.symlinked_dirs) == 25
    info = {"symlinked_dirs": snap.symlinked_dirs, "complete": True}
    assert am.under_unscanned_dir("data/link24/x.png", info)
    text = am.render_for_evaluator([], {"scope": "work_dir", "complete": True,
                                        "symlinked_dirs": snap.symlinked_dirs})
    assert "and 5 more." in text


def test_incomplete_listing_with_nothing_found_is_worded_as_partial():
    info = {"scope": "work_dir", "complete": False, "incomplete_reason": "cap",
            "stdout_paths": []}
    text = am.render_for_evaluator([], info)
    assert "No written file was found in the scanned part of the run directory" in text
    assert "No file in the run directory was written" not in text


def test_junk_files_and_tool_scratch_dirs_are_not_outputs(tmp_path):
    out = _run(tmp_path, """
        import os
        for d in ("dask-worker-space", "wandb", "mlruns", "work/lightning_logs"):
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "f.txt"), "w").write("x")
        os.makedirs("work", exist_ok=True)
        for n in (".nfs000123", ".~lock.table.csv#", "~$book.xlsx", "kept.csv"):
            open(os.path.join("work", n), "w").write("x")
        print("ok")
    """)
    assert _paths(out) == ["work/kept.csv"]


def test_colon_prefixed_paths_are_extracted_but_host_ports_are_not():
    toks = am.stdout_path_tokens("Saved to:/abs/answer.json; out:answer.json; "
                                 "serving localhost:8000/index.html; "
                                 "mirror www.example.com/data.csv, cdn.example.org/x.json")
    assert toks == ["/abs/answer.json", "answer.json"]


def test_a_timed_out_attempt_still_lists_what_it_wrote(tmp_path):
    from cmbagent_lg.planning.schemas import Step

    step = Step(sub_task="x", sub_task_agent="engineer", bullet_points=["x"],
                code_execution_timeout=1)
    out = _run(tmp_path, """
        import time
        open("partial.csv", "w").write("x")
        print("wrote partial.csv", flush=True)
        time.sleep(30)
    """, step=step)
    assert out["execution_timed_out"] is True
    assert _paths(out) == ["partial.csv"]
    assert out["data_manifest_info"]["stdout_paths"][0]["status"] == "written"


def test_a_static_refusal_clears_the_previous_listing(tmp_path):
    from cmbagent_lg.self_debug.static_preflight import static_preflight

    state = {"step": _step(), "work_dir": str(tmp_path), "step_number": 1, "attempts": 2,
             "current_code": EngineerResponse(python_code="def f(:\n", code_explanation="x"),
             "data_manifest": [{"path": "answer.json", "bytes": 2}],
             "data_manifest_info": {"scope": "work_dir", "complete": True}}
    out = static_preflight(state, _rt(PlanContext(main_task="t")))
    assert out["static_preflight_rejected"] == "syntax"
    assert out["data_manifest"] == [] and out["data_manifest_info"] is None
