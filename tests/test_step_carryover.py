"""Files carried over between attempts of one step.

A resumable script skips work an earlier attempt of the same step already saved. In the
m14-1 validation (qwen r2 step 1) attempt 5 wrote 61 `partial_results/*.json`, attempt 6
skipped them and wrote only `manifest.json`, `status.json` and its code, and stdout named
them as the glob `partial_results/*.json`; the open-model evaluators read "not written by
this attempt" as "not processed" and rejected the step. These tests run the real
executor twice with the graph state carried between the runs, as the self_debug graph
does (a real subprocess, no model, no network), and pin down: carried over vs written vs
pre-existing; stdout globs and directories counted by class; the evaluator wording; and
that `output_postcondition` still rejects a declared output only an earlier attempt
wrote.

Run:
    python -m pytest -q tests/test_step_carryover.py
"""

import os
import sys
import textwrap
import time
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import attempt_manifest as am
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.prompts import step_evaluator_instructions
from cmbagent_lg.self_debug.schemas import EngineerResponse

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

RESUMABLE = """
    import json, os
    os.makedirs("partial_results", exist_ok=True)
    done = 0
    for i in range({n}):
        p = f"partial_results/chunk_{{i:02d}}.json"
        if os.path.exists(p):
            continue
        open(p, "w").write(json.dumps({{"i": i}}))
        done += 1
    open("manifest.json", "w").write(json.dumps({{"chunks": {n}}}))
    print(f"processed {{done}} new chunk(s); results in partial_results/*.json")
    print("index at manifest.json, directory partial_results/")
"""


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.delenv(REMOTE_ENV, raising=False)
    for k in ("FILES", "DEPTH", "DIR_ENTRIES"):
        monkeypatch.delenv(f"CMBAGENT_LG_MANIFEST_MAX_{k}", raising=False)


def _rt(ctx=None):
    return types.SimpleNamespace(context=ctx or PlanContext(main_task="t"))


def _step(sub_task="process the chunks", *bullets):
    return Step(sub_task=sub_task, sub_task_agent="engineer",
                bullet_points=list(bullets) or ["x"])


class _Step:
    """One step's graph state across attempts, the way the graph carries it."""

    def __init__(self, work_dir, step=None):
        self.state = {"step": step or _step(), "step_number": 1, "work_dir": str(work_dir),
                      "attempts": 0}

    def attempt(self, code):
        self.state["attempts"] += 1
        self.state["current_code"] = EngineerResponse(
            python_code=textwrap.dedent(code), code_explanation="x")
        out = sd_nodes.executor(self.state, _rt())
        assert out["execution_returncode"] == 0, out["execution_stderr"]
        self.state.update(out)
        return out


def _history(work_dir, n=1):
    """The step's history file (what graph state only references)."""
    import json

    return json.loads((work_dir / "logs" / f"step_{n}_history.json").read_text())


def _writers(work_dir, n=1):
    """path -> attempt that last wrote it, from the history file."""
    return {k: v[1] for k, v in _history(work_dir, n)["writes"].items()}


def _paths(out):
    return [f["path"] for f in out["data_manifest"]]


def _carried(out):
    co = out["data_manifest_info"]["carried_over"]
    rows = list(co["listed"])
    return co, {r["path"]: r["attempt"] for r in rows}


def _checks(out):
    return {c["path"]: c for c in out["data_manifest_info"]["stdout_paths"]}


def _prompt(out, step=None):
    return step_evaluator_instructions(
        PlanContext(main_task="t"), step or _step(), stdout=out["execution_stdout"],
        data_manifest=out["data_manifest"], step_feedback_history=[],
        data_manifest_info=out["data_manifest_info"])


def test_a_resumable_second_attempt_shows_the_first_attempts_files_as_carried_over(tmp_path):
    run = _Step(tmp_path)
    first = run.attempt(RESUMABLE.format(n=5))
    assert len([p for p in _paths(first) if p.startswith("partial_results/")]) == 5
    assert first["data_manifest_info"]["carried_over"]["count"] == 0
    assert _writers(tmp_path)["partial_results/chunk_00.json"] == 1
    baseline = _history(tmp_path)["baseline"]
    assert baseline["files"] == {}
    # State carries only a reference to the history file, not the snapshot.
    ref = run.state["step_history"]
    assert set(ref) == {"path", "run", "key"} and ref["run"] == 1

    second = run.attempt(RESUMABLE.format(n=5))
    # data_manifest keeps its meaning: only what attempt 2 wrote.
    assert _paths(second) == ["manifest.json"]
    co, rows = _carried(second)
    assert co["count"] == 5
    assert rows == {f"partial_results/chunk_{i:02d}.json": 1 for i in range(5)}
    # The baseline is the step's, taken once, not attempt 2's own before-snapshot.
    assert _history(tmp_path)["baseline"] == baseline
    assert second["data_manifest_info"]["attempt"] == 2


def test_a_file_from_an_earlier_step_is_pre_existing_not_carried_over(tmp_path):
    step1 = _Step(tmp_path)
    step1.attempt("""
        open("prior_step.csv", "w").write("a,b")
        print("saved prior_step.csv")
    """)
    # Step 2 is a fresh graph invoke of another task: its history starts clean.
    step2 = _Step(tmp_path, _step("use the prior output"))
    code = """
        import os
        if not os.path.exists("mine.csv"):
            open("mine.csv", "w").write("x")
        print("read prior_step.csv; wrote mine.csv")
    """
    step2.attempt(code)
    second = step2.attempt(code)
    co, rows = _carried(second)
    assert rows == {"mine.csv": 1}
    checks = _checks(second)
    assert checks["prior_step.csv"]["status"] == "pre_existing"
    assert checks["mine.csv"]["status"] == "carried_over"
    assert checks["mine.csv"]["attempt"] == 1
    text = _prompt(second)
    assert "- prior_step.csv: exists, but was NOT written or modified by this attempt " \
           "or an earlier attempt of this step (it was on disk before this step began" in text
    assert ("- mine.csv: exists; written by attempt 1 and NOT rewritten by this attempt "
            "(carried over from earlier in this step)") in text


def test_a_file_rewritten_by_the_second_attempt_is_written_not_carried_over(tmp_path):
    run = _Step(tmp_path)
    run.attempt("""
        open("a.txt", "w").write("1")
        open("b.txt", "w").write("1")
        print("saved a.txt b.txt")
    """)
    second = run.attempt("""
        open("a.txt", "w").write("22")
        print("saved a.txt; reused b.txt")
    """)
    assert _paths(second) == ["a.txt"]
    _co, rows = _carried(second)
    assert rows == {"b.txt": 1}
    checks = _checks(second)
    assert checks["a.txt"]["status"] == "written"
    assert checks["b.txt"]["status"] == "carried_over"
    assert _writers(tmp_path) == {"a.txt": 2, "b.txt": 1}


def test_carryover_across_three_attempts_keeps_each_files_writer(tmp_path):
    run = _Step(tmp_path)
    run.attempt("open('one.txt','w').write('1'); print('one.txt')")
    run.attempt("open('two.txt','w').write('2'); print('two.txt')")
    third = run.attempt("print('nothing new: one.txt two.txt')")
    _co, rows = _carried(third)
    assert rows == {"one.txt": 1, "two.txt": 2}


def test_a_file_changed_during_the_step_by_no_recorded_attempt_is_carried_over_unnumbered(tmp_path):
    run = _Step(tmp_path)
    run.attempt("print('attempt 1 writes nothing')")
    # Something between attempts (an escalation agent, say) wrote a file.
    (tmp_path / "between.txt").write_text("x")
    second = run.attempt("print('uses between.txt')")
    _co, rows = _carried(second)
    assert rows == {"between.txt": None}
    assert "an earlier attempt of this step (attempt not recorded)" in _prompt(second)


def test_a_stdout_glob_and_directory_expand_into_bounded_class_counts(tmp_path):
    (tmp_path / "partial_results").mkdir()
    (tmp_path / "partial_results" / "seed.json").write_text("{}")  # before the step
    run = _Step(tmp_path)
    run.attempt(RESUMABLE.format(n=61))
    second = run.attempt(RESUMABLE.format(n=64))  # three new chunks
    checks = _checks(second)
    glob = checks["partial_results/*.json"]
    assert glob["kind"] == "glob" and glob["total"] == 65
    assert glob["written"] == 3 and glob["carried_over"] == {"1": 61}
    assert glob["pre_existing"] == 1
    d = checks["partial_results/"]
    assert d["kind"] == "directory" and d["total"] == 65
    text = _prompt(second)
    assert ("- partial_results/*.json (pattern): 65 file(s): 3 written by this attempt; "
            "61 carried over from attempt 1 (unchanged by this attempt); 1 on disk before "
            "this step began.") in text
    # Bounded: the chunk files are counted, never listed one by one in the prompt.
    assert text.count("partial_results/chunk_") <= am.MAX_RENDERED


def test_the_carried_over_section_is_summarised_per_directory_when_long(tmp_path):
    run = _Step(tmp_path)
    run.attempt(RESUMABLE.format(n=61))
    second = run.attempt(RESUMABLE.format(n=61))
    co = second["data_manifest_info"]["carried_over"]
    assert co["count"] == 61 and co["listed"] == []
    assert co["groups"] == [{"dir": "partial_results/", "attempt": 1, "files": 61,
                             "bytes": co["bytes"]}]
    text = _prompt(second)
    assert am.CARRIED_OVER_HEADER in text
    assert "61 file(s), " in text
    assert "- partial_results/: 61 file(s) from attempt 1, " in text
    assert "partial_results/chunk_" not in text
    # The section comes after the OUTPUT FILES block.
    assert text.index("OUTPUT FILES WRITTEN BY THIS ATTEMPT") < text.index(am.CARRIED_OVER_HEADER)


def test_a_short_carried_over_set_is_listed_with_its_attempt(tmp_path):
    run = _Step(tmp_path)
    run.attempt(RESUMABLE.format(n=3))
    text = _prompt(run.attempt(RESUMABLE.format(n=3)))
    assert "- partial_results/chunk_00.json (" in text and "; from attempt 1)" in text


def test_no_carried_over_section_on_a_first_attempt_or_without_history(tmp_path):
    run = _Step(tmp_path)
    first = run.attempt(RESUMABLE.format(n=2))
    assert am.CARRIED_OVER_HEADER not in _prompt(first)
    # An info dict from before this field existed renders as before.
    info = dict(first["data_manifest_info"])
    info.pop("carried_over")
    assert am.render_carried_over(info) == ""
    assert am.render_carried_over(None) == ""


def test_the_postcondition_still_rejects_a_declared_output_only_an_earlier_attempt_wrote(tmp_path):
    from cmbagent_lg.self_debug.output_postcondition import output_postcondition

    step = _step("summarise", "save the summary table to data/summary.csv")
    run = _Step(tmp_path, step)
    run.attempt("""
        import os
        os.makedirs("data", exist_ok=True)
        open("data/summary.csv", "w").write("a,b")
        print("saved data/summary.csv")
    """)
    second = run.attempt("""
        import os
        assert os.path.exists("data/summary.csv")
        print("data/summary.csv already there, skipped")
    """)
    assert "data/summary.csv" not in _paths(second)
    assert _checks(second)["data/summary.csv"]["status"] == "carried_over"
    state = dict(run.state, execution_returncode=0, execution_timed_out=False)
    res = output_postcondition(state, _rt())
    assert res["output_postcondition_rejected"] == "missing_output"
    assert "earlier attempt of this step saved" in res["execution_stderr"]


def test_an_absolute_stdout_path_outside_the_run_dir_carries_over_by_attempt(
        tmp_path, tmp_path_factory, monkeypatch):
    # Outside the walk the check is by change time; without slack and with a pause, the
    # first attempt's write reads as older than the second attempt's start.
    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
    outside = tmp_path_factory.mktemp("outside") / "answer.json"
    run = _Step(tmp_path / "run")
    run.attempt(f"open({str(outside)!r}, 'w').write('1'); print('saved {outside}')")
    time.sleep(0.05)
    second = run.attempt(f"print('answer already at {outside}')")
    c = _checks(second)[str(outside)]
    assert c["status"] == "carried_over" and c["attempt"] == 1
    _co, rows = _carried(second)
    assert rows == {str(outside): 1}


def test_a_re_run_of_the_same_attempt_is_worded_as_such(tmp_path):
    run = _Step(tmp_path)
    run.attempt("open('x.txt','w').write('1'); print('x.txt')")
    # escalation -> executor re-runs attempt 1 without bumping the counter
    run.state["attempts"] -= 1
    again = run.attempt("print('x.txt reused')")
    assert "an earlier run of this attempt (1, before it was re-run)" in _prompt(again)


def test_glob_and_directory_tokens_are_conservative(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "a.csv").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    stdout = ("**bold** 2*np.pi/3 and/or km/s https://x.org/a/ www.example.com/data/ "
              "saved results/ and `results/*.csv`, also /usr/lib/ and data/*.png.")
    groups = am.expand_stdout_groups(stdout, tmp_path, tmp_path, walk, (), {})
    # data/*.png matches nothing and there is no data/ directory: not reported.
    assert [(g["path"], g["kind"], g["total"]) for g in groups] == [
        ("results/", "directory", 1), ("results/*.csv", "glob", 1)]
    assert all(g["pre_existing"] == g["total"] for g in groups)


def test_the_number_of_globs_counted_is_capped(tmp_path):
    for i in range(50):
        (tmp_path / f"d{i}").mkdir()
    walk = am.snapshot_tree(tmp_path)
    stdout = " ".join(f"d{i}/*.csv" for i in range(50))
    assert len(am.expand_stdout_groups(stdout, tmp_path, tmp_path, walk, (), {})) == \
        am.MAX_STDOUT_GROUPS


def test_the_recorded_writes_are_bounded_by_the_cap(tmp_path):
    for name in [f"a{i}" for i in range(10)] + ["b"]:
        (tmp_path / name).write_text("x")
    walk = am.snapshot_tree(tmp_path)
    h = am.StepHistory({}, None, attempt=1)
    h.record([{"path": f"a{i}"} for i in range(10)], walk, tmp_path, cap=8)
    h.attempt = 2
    h.record([{"path": "b"}, {"path": "a0"}], walk, tmp_path, cap=8)
    assert len(h.writes) == 8 and h.writes["b"][1] == 2 and h.writes["a0"][1] == 2


def test_a_remote_attempt_neither_records_nor_resets_the_step_history(tmp_path, monkeypatch):
    calls = {}

    class _Fake:
        @classmethod
        def from_env(cls, spec):
            return cls()

        def run_code(self, session, n, files, timeout):
            calls["n"] = n
            return {"stdout": "ok", "stderr": "", "returncode": 0, "manifest": []}

    import cmbagent_lg.self_debug.remote_exec as rx

    run = _Step(tmp_path)
    run.attempt("open('x.txt','w').write('1'); print('saved x.txt')")
    before = (tmp_path / "logs" / "step_1_history.json").read_text()
    ref = run.state["step_history"]

    monkeypatch.setattr(rx, "RemoteExecutor", _Fake)
    monkeypatch.setenv(REMOTE_ENV, "127.0.0.1:1")
    remote = run.attempt("print(1)")
    assert calls["n"] == 1
    # The guest's data is not the engine's run directory: nothing recorded, the
    # reference kept, the file untouched.
    assert "step_history" not in remote
    assert "carried_over" not in remote["data_manifest_info"]
    assert run.state["step_history"] == ref
    assert (tmp_path / "logs" / "step_1_history.json").read_text() == before

    monkeypatch.delenv(REMOTE_ENV)
    third = run.attempt("print('x.txt reused')")
    assert _carried(third)[1] == {"x.txt": 1}


def test_the_template_names_the_three_kinds():
    text = step_evaluator_instructions(
        PlanContext(main_task="t"), _step(), stdout="x", data_manifest=[],
        step_feedback_history=[], data_manifest_info=None)
    flat = " ".join(text.split())
    assert '"not written by this attempt" does NOT mean "not processed"' in flat
    assert "CARRIED OVER from an earlier attempt of this SAME step" in text
    assert "ON DISK BEFORE THIS STEP BEGAN" in text
    assert "FILES CARRIED OVER FROM EARLIER ATTEMPTS" not in text
    assert text.rstrip().endswith("-" * 45)


def test_slash_words_that_are_not_directories_do_not_use_up_the_glob_slots(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "a.csv").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    noise = " ".join(f"w{i}/x{i}" for i in range(30))
    groups = am.expand_stdout_groups("saved results/ " + noise, tmp_path, tmp_path, walk,
                                     (), {})
    assert [g["path"] for g in groups] == ["results/"]


def test_a_glob_printed_relative_to_a_subdirectory_matches_by_suffix(tmp_path):
    (tmp_path / "work" / "chunks").mkdir(parents=True)
    (tmp_path / "work" / "chunks" / "c1.npz").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    groups = am.expand_stdout_groups("wrote chunks/*.npz", tmp_path, tmp_path, walk,
                                     {"work/chunks/c1.npz"}, {})
    assert groups[0]["total"] == 1 and groups[0]["written"] == 1


# ── review fixes: restart, signatures, failure isolation, glob precision, wording ──


def test_a_restarted_step_reads_its_killed_runs_files_as_an_earlier_run_of_the_step(tmp_path):
    (tmp_path / "input.csv").write_text("a,b")  # provided before the step began
    killed = _Step(tmp_path)
    killed.attempt("print('attempt 1 wrote nothing')")
    killed.attempt(RESUMABLE.format(n=3))
    # The job is killed; deep_research resumes at this step with a fresh graph invoke.
    resumed = _Step(tmp_path)
    out = resumed.attempt(RESUMABLE.format(n=3) + "    print('read input.csv')\n")
    assert resumed.state["step_history"]["run"] == 2
    _co, rows = _carried(out)
    assert rows == {f"partial_results/chunk_{i:02d}.json": "prev:2" for i in range(3)}
    checks = _checks(out)
    assert checks["input.csv"]["status"] == "pre_existing"
    assert checks["partial_results/*.json"]["carried_over"] == {"prev:2": 3}
    text = _prompt(out)
    assert ("from attempt 2 of an earlier run of this same step (before the step was "
            "restarted)") in text
    assert "3 carried over from attempt 2 of an earlier run of this same step" in text
    # The first run's baseline is kept.
    assert _history(tmp_path)["runs"] == 2


def test_a_re_planned_step_with_the_same_number_does_not_inherit_the_history(tmp_path):
    first = _Step(tmp_path)
    first.attempt("open('old.txt','w').write('1'); print('saved old.txt')")
    replanned = _Step(tmp_path, _step("a different task"))
    out = replanned.attempt("print('read old.txt')")
    assert replanned.state["step_history"]["run"] == 1
    assert _checks(out)["old.txt"]["status"] == "pre_existing"
    assert out["data_manifest_info"]["carried_over"]["count"] == 0


def test_a_file_changed_after_its_attempt_wrote_it_is_not_attributed_to_that_attempt(tmp_path):
    run = _Step(tmp_path)
    run.attempt("open('x.txt','w').write('1'); print('saved x.txt')")
    (tmp_path / "x.txt").write_text("22")  # an escalation agent edits it between runs
    run.state["attempts"] -= 1  # escalation re-runs attempt 1
    again = run.attempt("print('x.txt reused')")
    assert _carried(again)[1] == {"x.txt": None}
    text = _prompt(again)
    assert "earlier run of this attempt" not in text
    assert "an earlier attempt of this step (attempt not recorded)" in text


def test_a_deleted_file_leaves_the_record_and_a_recreation_is_not_attributed(tmp_path):
    run = _Step(tmp_path)
    run.attempt("open('x.txt','w').write('1'); print('saved x.txt')")
    second = run.attempt("import os; os.remove('x.txt'); print('removed x.txt')")
    assert "x.txt" not in _writers(tmp_path)
    assert _checks(second)["x.txt"]["status"] == "missing" or \
        _checks(second)["x.txt"]["status"] == "not_found"
    assert second["data_manifest_info"]["carried_over"]["count"] == 0
    (tmp_path / "x.txt").write_text("other")  # recreated by something else
    third = run.attempt("print('x.txt reused')")
    assert _carried(third)[1] == {"x.txt": None}


def test_a_file_rewritten_by_attempt_3_reads_as_from_3_at_attempt_4(tmp_path):
    run = _Step(tmp_path)
    run.attempt("open('x.txt','w').write('1'); print('x.txt')")
    run.attempt("print('x.txt kept')")
    run.attempt("open('x.txt','w').write('333'); print('x.txt rewritten')")
    fourth = run.attempt("print('x.txt kept')")
    assert _carried(fourth)[1] == {"x.txt": 3}
    assert _checks(fourth)["x.txt"]["attempt"] == 3


def test_a_truncated_baseline_still_tells_pre_existing_from_carried_over(tmp_path, monkeypatch):
    monkeypatch.setenv("CMBAGENT_LG_MANIFEST_MAX_FILES", "5")
    monkeypatch.setattr(am, "MTIME_SLACK_NS", 0)
    for i in range(6):
        (tmp_path / f"p{i}.txt").write_text("x")
    time.sleep(0.05)
    run = _Step(tmp_path)
    run.attempt("open('a.txt','w').write('1'); print('saved a.txt')")
    assert _history(tmp_path)["baseline"]["truncated"]
    second = run.attempt("print('read a.txt p0.txt p5.txt')")
    checks = _checks(second)
    assert checks["a.txt"]["status"] == "carried_over" and checks["a.txt"]["attempt"] == 1
    assert checks["p0.txt"]["status"] == "pre_existing"  # in the baseline, unchanged
    assert checks["p5.txt"]["status"] == "pre_existing"  # past the cap, older than the step


def test_a_failure_in_the_carry_over_split_keeps_the_manifest(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(am, "_classify_unwritten", boom)
    run = _Step(tmp_path)
    out = run.attempt("open('x.txt','w').write('1'); print('saved x.txt')")
    assert _paths(out) == ["x.txt"]
    info = out["data_manifest_info"]
    assert info["complete"] is True and info["history_error"] == "RuntimeError"
    assert info["carried_over"]["count"] == 0
    assert _checks(out)["x.txt"]["status"] == "written"


def test_wildcard_only_tokens_are_not_globs(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    stdout = "/* comment */ and **/ and *.* and saved *.txt"
    groups = am.expand_stdout_groups(stdout, tmp_path, tmp_path, walk, (), {})
    assert [g["path"] for g in groups] == ["*.txt"]


def test_a_suffix_matched_glob_says_where_it_matched(tmp_path):
    (tmp_path / "old" / "data").mkdir(parents=True)
    (tmp_path / "old" / "data" / "a.png").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    groups = am.expand_stdout_groups("saved figures to data/*.png", tmp_path, tmp_path,
                                     walk, (), {})
    assert groups[0]["matched_by"] == "suffix"
    assert groups[0]["matched_dirs"] == ["old/data/"]
    line = am._group_line(groups[0], 1)
    assert "no such directory at the script's working directory; matched by name under " \
           "old/data/" in line
    # With data/ present at the cwd, the pattern is read there and nothing else.
    (tmp_path / "data").mkdir()
    walk = am.snapshot_tree(tmp_path)
    groups = am.expand_stdout_groups("saved figures to data/*.png", tmp_path, tmp_path,
                                     walk, (), {})
    assert groups[0]["total"] == 0 and "matched_by" not in groups[0]


def test_unanchored_globs_that_match_nothing_are_dropped(tmp_path):
    walk = am.snapshot_tree(tmp_path)
    stdout = "flux in W/m*2.csv, ratio a*b.dat"
    assert am.expand_stdout_groups(stdout, tmp_path, tmp_path, walk, (), {}) == []
    # After a save verb, a pattern at the cwd that matches nothing is a claim to check.
    groups = am.expand_stdout_groups("saved chunk_*.npz", tmp_path, tmp_path, walk, (), {})
    assert [(g["path"], g["total"]) for g in groups] == [("chunk_*.npz", 0)]


def test_with_an_empty_data_dir_the_carried_over_section_says_they_were_generated(tmp_path):
    run = _Step(tmp_path)
    run.attempt("""
        import os
        os.makedirs("data", exist_ok=True)
        open("data/input.csv", "w").write("a,b")
        print("made data/input.csv")
    """)
    second = run.attempt("print('loaded data/input.csv')")
    assert run.state.get("input_dir_empty") is True
    flat = lambda t: " ".join(t.split())
    with_fact = step_evaluator_instructions(
        PlanContext(main_task="t"), _step(), stdout=second["execution_stdout"],
        data_manifest=second["data_manifest"], step_feedback_history=[],
        input_dir_empty=True, data_manifest_info=second["data_manifest_info"])
    assert ("data/ was EMPTY before this step's first attempt ran, so every file above "
            "was generated within this step") in flat(with_fact)
    assert "data/ was EMPTY before this step's first attempt" not in flat(_prompt(second))


def test_without_history_a_file_not_written_is_not_claimed_to_predate_the_step(tmp_path):
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("x")
    walk = am.snapshot_tree(tmp_path)
    manifest, info = am.build(walk, walk, time.time_ns(), "saved r/*.csv and b.csv",
                              tmp_path, tmp_path, history=None)
    assert info["history"] is False
    text = am.render_for_evaluator(manifest, info)
    assert "before this step began" not in text
    assert "1 not written by this attempt" in text
    assert "- b.csv: exists, but was NOT written or modified by this attempt (it was " \
           "already on disk before the run)" in text


def test_the_graph_keeps_the_baseline_across_a_refused_attempt_and_shows_carry_over(
        tmp_path, monkeypatch):
    """The compiled self_debug graph with a scripted engineer and evaluator: attempt 1 is
    refused by static_preflight (it never prints), attempt 2 writes the chunks and is
    judged not fulfilled, attempt 3 resumes. Attempt 3's evaluator prompt carries the
    chunks over from attempt 2, and the history file holds one baseline."""
    from cmbagent_lg.self_debug.graph import graph
    from cmbagent_lg.self_debug.schemas import ExecutionVerdict, StepVerdict

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    (tmp_path / "given.csv").write_text("a,b")
    scripts = iter([
        "x = 1\n",
        textwrap.dedent(RESUMABLE.format(n=4)),
        textwrap.dedent(RESUMABLE.format(n=4)),
    ])
    prompts = []

    class _Proposer:
        def invoke(self, msgs, config=None):
            return types.SimpleNamespace(text=next(scripts))

    class _Formatter:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

    class _Critic:
        def __init__(self):
            self.schema = None

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, msgs, config=None):
            if self.schema is ExecutionVerdict:
                return ExecutionVerdict(status="success")
            prompts.append("\n".join(str(m.content) for m in msgs))
            if am.CARRIED_OVER_HEADER not in prompts[-1]:
                return StepVerdict(fulfilled=False, unmet_requirements=["x"],
                                   feedback="print the chunk count")
            return StepVerdict(fulfilled=True)

    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())
    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False,
                      vlm_enabled=False, max_n_attempts=5)
    out = graph.invoke({"step": _step(), "step_number": 1, "work_dir": str(tmp_path)},
                       context=ctx)
    # Attempt 1 (refused, judged without a run), attempt 2 (wrote the chunks), attempt 3.
    assert out["attempts"] == 3 and len(prompts) == 3
    assert all(am.CARRIED_OVER_HEADER not in p for p in prompts[:2])
    last = prompts[2]
    assert "- partial_results/chunk_00.json (" in last
    assert "from attempt 2)" in last
    assert "given.csv" not in last.split(am.CARRIED_OVER_HEADER)[1].split("-" * 45)[0]
    hist = _history(tmp_path)
    assert hist["runs"] == 1
    assert set(hist["baseline"]["files"]) == {"given.csv"}
    # State holds the reference only.
    assert set(out["step_history"]) == {"path", "run", "key"}
