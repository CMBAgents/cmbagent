"""Declared outputs, protected files, and a disagreement with the step evaluator.

The measured failure: a step's bullet read "Atomically save `data/<this>.json` with the
same structure ... Do not overwrite or delete `data/<earlier>.json` ...". The
post-condition scanned the whole bullet for a save verb before each path, so the
protected file counted as a declared output. The attempt that wrote only this step's file
was bounced for not writing the protected one; the next attempt obeyed and rewrote it,
and the step evaluator rejected that for overwriting a protected file. Six attempts
alternated and the step failed; the replanned step failed the same way on "Do not read,
validate, ..., write, re-save, or otherwise touch `data/<earlier>.json`, ...". On the
attempts that loaded this step's own file (saved by an earlier attempt, as the sub-task
allowed), the post-condition also demanded a rewrite.

Pinned down here, with neutral file names and the same sentence structure:
  (1) how a sentence is read: declared, protected, unsure (neither), nothing;
  (2) a protected file the attempt modified or deleted is bounced with no model call;
  (3) a declared output saved by an earlier attempt of the same run of the step that
      exited 0, unchanged since, counts; a stale file from before the step, a failed
      attempt's file, a changed file and an earlier run's file still do not;
  (4) a verdict that contradicts the post-condition about a path wins, for the rest of
      the step, and the engineer is told both messages;
  (5) the replay: the old reading alternates until the attempts run out, the new one
      passes at attempt 1.

Real executor (a subprocess per attempt), no model, no network.

Run:
    python -m pytest -q tests/test_output_postcondition_conflicts.py
"""

import os
import re
import sys
import textwrap
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import attempt_manifest as am
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug import output_postcondition as op
from cmbagent_lg.self_debug.output_postcondition import (
    POSTCONDITION_MARKER,
    arbitrate_with_step_evaluator,
    changed_protected_paths,
    declared_output_paths,
    evaluator_objects_to_writing,
    evaluator_requires_writing,
    output_postcondition,
    protected_paths,
    read_step_paths,
)
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, StepVerdict

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
CONFLICT_LOG = "[output_postcondition] conflict with the step evaluator on {}: evaluator's reading stands"


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.delenv(REMOTE_ENV, raising=False)
    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    for k in ("FILES", "DEPTH", "DIR_ENTRIES"):
        monkeypatch.delenv(f"CMBAGENT_LG_MANIFEST_MAX_{k}", raising=False)


def _rt(ctx=None):
    return types.SimpleNamespace(context=ctx or PlanContext(main_task="t"))


def _step(sub_task, *bullets):
    return Step(sub_task=sub_task, sub_task_agent="engineer", bullet_points=list(bullets))


# ── the two steps of the measured case, with neutral names ────────────────────

# Step 3: this step's file is data/b.json; data/a.json is an earlier step's.
SAVE_AND_PROTECT = (
    "Atomically save `data/b.json` with the same schema-independent but consistent "
    "diagnostic structure used for the first item: source path, table name, size, "
    "summary values, timings, and `ok`. Do not overwrite or delete `data/a.json` or any "
    "other earlier artifacts.")
STEP_3 = _step(
    "Compute the summary table for the second item.",
    "Work on the second item only. The source file is `inputs/second/source.csv`, "
    "table `values`.",
    "If `data/b.json` already exists and validates, load it, print its summary values, "
    "and skip recomputation. Otherwise compute it using the same method described for "
    "the first item.",
    "Read the table and check that every entry is finite. Record the arithmetic mean.",
    SAVE_AND_PROTECT,
    "Require all summary values to be finite and strictly positive for a successful "
    "step. If not, save an `ok = false` diagnostic artifact and fail clearly.",
)
# Step 4, written by the replanner after step 3 failed: read-only on the same file.
TOUCH_NOTHING = (
    "Do not read, validate, summarize, normalize, write, re-save, or otherwise touch "
    "`data/a.json`, `data/c.json`, or any other intermediate artifact in this step.")
STEP_4 = _step(
    "Read-only verification of the existing checkpoint of the second item; recompute "
    "only if that checkpoint is missing or invalid.",
    "Work on the second item only.",
    "Operate exclusively on the single checkpoint path `data/b.json`.",
    TOUCH_NOTHING,
    "Do not use a loop over several items, a glob over `data/*.json`, or any code path "
    "that could reach the first item.",
    "If `data/b.json` exists and passes the existing validation rules (`ok == true`, "
    "finite values of the expected length), then print the stored summary values and "
    "exit successfully without writing anything at all.",
    "If recomputation is needed, atomically save only `data/b.json`.",
    "Do not overwrite, delete, normalize, or re-save any other existing artifact.",
)


# ── the reading before this change, kept here only ────────────────────────────

_OLD_PATH_RE = re.compile(
    r"(?<![\w/.\-])(?:\./)?(data/[\w][\w./\-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/\-])")
_OLD_SAVE_RE = re.compile(
    r"\b(sav(?:e|es|ed|ing)|writ(?:e|es|ing)|written|stor(?:e|es|ed|ing)"
    r"|export(?:s|ed|ing)?|output(?:s|ted|ting)?|dump(?:s|ed|ing)?"
    r"|persist(?:s|ed|ing)?|produc(?:e|es|ed|ing)|generat(?:e|es|ed|ing)"
    r"|creat(?:e|es|ed|ing)|to_csv|to_parquet|to_pickle|savefig|np\.save)\b",
    re.IGNORECASE)
_OLD_LOAD_RE = re.compile(
    r"\b(load(?:s|ed|ing)?|read(?:s|ing)?|open(?:s|ed|ing)?|import(?:s|ed|ing)?"
    r"|pars(?:e|es|ed|ing)|ingest(?:s|ed|ing)?|input(?:s)?|provided|given"
    r"|available|existing|supplied|fetch(?:es|ed|ing)?)\b",
    re.IGNORECASE)


def old_declared_output_paths(step):
    """`declared_output_paths` as it was: the nearest preceding verb in the WHOLE text
    of a bullet decides, and a negation is not seen."""
    texts = [getattr(step, "sub_task", "") or ""]
    texts += [b or "" for b in (getattr(step, "bullet_points", None) or [])]
    out = []
    for text in texts:
        if not text:
            continue
        saves = [m.end() for m in _OLD_SAVE_RE.finditer(text)]
        loads = [m.end() for m in _OLD_LOAD_RE.finditer(text)]
        for m in _OLD_PATH_RE.finditer(text):
            at = m.start()
            last_save = max([e for e in saves if e <= at], default=None)
            last_load = max([e for e in loads if e <= at], default=None)
            if last_save is None:
                continue
            if last_load is not None and last_load > last_save:
                continue
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def old_output_postcondition(state, _rt=None):
    """The node as it was, reduced to what the replay needs: a declared path this
    attempt's manifest does not list is missing, whoever wrote the copy on disk."""
    out = {"output_postcondition_rejected": None}
    if state.get("execution_returncode") != 0:
        return out
    names = am.written_names(state.get("data_manifest"))
    missing = [p for p in old_declared_output_paths(state["step"])
               if p not in names and os.path.basename(p) not in names]
    if missing:
        out.update({
            "output_postcondition_rejected": "missing_output",
            "execution_returncode": 1,
            "output_postcondition_bounces": list(
                state.get("output_postcondition_bounces") or [])
            + [{"attempt": state["attempts"], "missing": missing, "changed": []}],
        })
    return out


# ── (1) how a sentence is read ────────────────────────────────────────────────


def _read(*bullets):
    r = read_step_paths(_step("do the step", *bullets))
    return r.declared, r.protected, sorted(r.unsure)


@pytest.mark.parametrize("bullet,declared,protected,unsure", [
    # The measured bullet: an affirmative sentence, then a prohibition.
    (SAVE_AND_PROTECT, ["data/b.json"], ["data/a.json"], []),
    # A list of verbs under one prohibition, and a list of paths after it.
    (TOUCH_NOTHING, [], ["data/a.json", "data/c.json"], []),
    ("Do not overwrite or delete `data/a.json`.", [], ["data/a.json"], []),
    ("Never overwrite the existing `data/a.json`.", [], ["data/a.json"], []),
    ("Take care not to overwrite `data/a.json`.", [], ["data/a.json"], []),
    ("Do **not** overwrite `data/a.json`.", [], ["data/a.json"], []),
    ("Avoid overwriting or deleting `data/a.json`.", [], ["data/a.json"], []),
    ("Make no changes to `data/a.json`.", [], ["data/a.json"], []),
    ("Do not modify and re-save `data/a.json`.", [], ["data/a.json"], []),
    ("Do not overwrite `data/a.json` or delete `data/c.json`.",
     [], ["data/a.json", "data/c.json"], []),
    ("Do not overwrite `data/a.json` because later steps read it.", [], ["data/a.json"], []),
    ("Do not modify `data/a.json` in this step (it is an earlier result).",
     [], ["data/a.json"], []),
    ("Do not touch:\n- `data/a.json`\n- `data/c.json`", [], ["data/a.json", "data/c.json"], []),
    # "without" inside an affirmative sentence.
    ("Save `data/b.json` without modifying `data/a.json`.", ["data/b.json"], ["data/a.json"], []),
    ("Write the summary to `data/b.json` without touching `data/a.json`, `data/c.json` "
     "or any other file.", ["data/b.json"], ["data/a.json", "data/c.json"], []),
    ("Write `data/b.json` instead of overwriting `data/a.json`.",
     ["data/b.json"], ["data/a.json"], []),
    # Protection stated after the path.
    ("Leave `data/a.json` untouched.", [], ["data/a.json"], []),
    ("`data/a.json` must not be modified.", [], ["data/a.json"], []),
    ("Keep `data/a.json` and `data/c.json` unchanged; write `data/b.json`.",
     ["data/b.json"], ["data/a.json", "data/c.json"], []),
    ("Load `data/a.json` (read-only) and write the summary to data/b.json.",
     ["data/b.json"], ["data/a.json"], []),
    # "but" closes the prohibition.
    ("Do not overwrite `data/a.json`, but save the result to `data/b.json`.",
     ["data/b.json"], ["data/a.json"], []),
    # A prohibition on reading or using a file protects nothing and declares nothing.
    ("Do not use the cached copy in `data/a.json`.", [], [], []),
    ("Do not read or load `data/a.json`.", [], [], []),
    # Not prohibitions: a double negative, "not only", a noun after "without".
    ("Do not forget to save `data/b.json`.", ["data/b.json"], [], []),
    ("Do not only save `data/b.json`, also save `data/d.json`.",
     ["data/b.json", "data/d.json"], [], []),
    ("Save the table without the index to data/t.csv", ["data/t.csv"], [], []),
    ("Without dropping any rows, compute the mean and save it to data/m.json",
     ["data/m.json"], [], []),
    # Unsure: a second verb phrase after the prohibited one, joined by a comma or "and".
    ("Do not overwrite `data/a.json`, save the result to `data/b.json`.",
     [], ["data/a.json"], ["data/b.json"]),
    ("Do not delete `data/a.json` and write the summary to `data/b.json`.",
     [], ["data/a.json"], ["data/b.json"]),
    ("Do not wait, save `data/b.json`.", [], [], ["data/b.json"]),
    ("Do not write the header twice into `data/b.json`.", [], [], ["data/b.json"]),
    ("Save the summary but do not use the cached copy in `data/a.json`.",
     [], [], ["data/a.json"]),
    ("Without re-running and saving `data/a.json`, print the stored values.",
     [], [], ["data/a.json"]),
    # Unsure: a condition governs the save or the prohibition.
    ("If recomputation is needed, atomically save only `data/b.json`.", [], [], ["data/b.json"]),
    ("Save `data/b.json` if it does not exist.", [], [], ["data/b.json"]),
    ("Otherwise compute it and save `data/b.json`.", [], [], ["data/b.json"]),
    ("You may save intermediate results to `data/tmp.npz`.", [], [], ["data/tmp.npz"]),
    ("Do not overwrite `data/a.json` unless it is invalid.", [], [], ["data/a.json"]),
    # Unsure: the words after the path qualify the prohibition (a manner, a content),
    # so it does not say the file itself must stay as it is.
    ("Do not write `data/b.json` in text mode.", [], [], ["data/b.json"]),
    ("Do not overwrite `data/a.json` with rounded values.", [], [], ["data/a.json"]),
    ("Avoid rewriting `data/b.json` on every iteration.", [], [], ["data/b.json"]),
    ("Do not delete rows in `data/a.json`.", [], [], ["data/a.json"]),
    ("Do not change the layout of `data/a.json`.", [], [], ["data/a.json"]),
    # Not conditions.
    ("Save `data/b.json` even if it is empty.", ["data/b.json"], [], []),
    ("Save the result to `data/b.json`, creating the directory if needed.",
     ["data/b.json"], [], []),
    # Nothing: a path with no save verb in its own sentence.
    ("Operate exclusively on the single checkpoint path `data/b.json`.", [], [], []),
    ("If `data/b.json` exists and passes the rules, then print the stored values and "
     "exit successfully without writing anything at all.", [], [], []),
    # Sentence boundaries: an abbreviation and a dotted name are not one.
    ("save the table, e.g. to data/t.csv", ["data/t.csv"], [], []),
    ("Save a checkpoint at `data/a.json`, using an atomic write (`tmp` file then "
     "`os.replace`). Include at least: `name`; and an `ok` flag. Do not delete or "
     "overwrite earlier artifacts.", ["data/a.json"], [], []),
    ("Save the following files:\n- `data/b.json`\n- `data/d.json`",
     ["data/b.json", "data/d.json"], [], []),
])
def test_how_a_sentence_is_read(bullet, declared, protected, unsure):
    assert _read(bullet) == (declared, protected, unsure)


def test_the_two_measured_steps():
    """Step 3 declares its own file and protects the earlier one. Step 4 declares
    nothing (its only save is conditional), protects the two files it must not touch,
    and its own file is unsure."""
    r3 = read_step_paths(STEP_3)
    assert (r3.declared, r3.protected, r3.unsure) == (["data/b.json"], ["data/a.json"], {})
    assert declared_output_paths(STEP_3) == ["data/b.json"]
    assert protected_paths(STEP_3) == ["data/a.json"]
    assert r3.protected_by["data/a.json"].startswith("Do not overwrite or delete")
    r4 = read_step_paths(STEP_4)
    assert (r4.declared, r4.protected) == ([], ["data/a.json", "data/c.json"])
    assert list(r4.unsure) == ["data/b.json"]
    # What the earlier reading made of the same text: every path a declared output.
    assert old_declared_output_paths(STEP_3) == ["data/b.json", "data/a.json"]
    assert old_declared_output_paths(STEP_4) == ["data/a.json", "data/c.json", "data/b.json"]


def test_across_sentences_of_one_step_a_contradiction_or_a_doubt_is_unsure():
    # One bullet saves it, another protects it: neither.
    assert _read("Save `data/b.json`.", "Do not overwrite `data/b.json`.") == \
        ([], [], ["data/b.json"])
    # One bullet saves it plainly, another only under a condition: neither.
    assert _read("Save `data/b.json`.", "If the check fails, rewrite and save `data/b.json`.") \
        == ([], [], ["data/b.json"])
    # One bullet protects it, another may rewrite it: not protected.
    assert _read("Do not overwrite `data/a.json`.",
                 "If `data/a.json` is invalid, save `data/a.json` again.") == \
        ([], [], ["data/a.json"])
    # An input mention elsewhere changes nothing.
    assert _read("Save `data/b.json`.", "Later steps load `data/b.json`.") == \
        (["data/b.json"], [], [])
    assert _read("Load `data/a.json`.", "Do not overwrite `data/a.json`.") == \
        ([], ["data/a.json"], [])


def test_the_unsure_paths_are_logged_once_per_step(tmp_path, capsys):
    ctx = PlanContext(main_task="t")
    state = {"step": STEP_4, "work_dir": str(tmp_path), "step_number": 4, "attempts": 1,
             "execution_returncode": 0, "execution_timed_out": False, "data_manifest": []}
    out = output_postcondition(state, _rt(ctx))
    assert out["output_postcondition_rejected"] is None
    err = capsys.readouterr().err
    assert ("[output_postcondition] step 4: not sure whether data/b.json is a declared "
            "output or a protected file") in err
    assert "treated as neither" in err
    state.update(out, attempts=2)
    output_postcondition(state, _rt(ctx))
    assert "not sure whether" not in capsys.readouterr().err


# ── a step run attempt by attempt, with the real executor ─────────────────────

WRITE_OWN = """
    import json, os
    os.makedirs("data", exist_ok=True)
    with open("data/b.json.tmp", "w") as f:
        json.dump({"item": "second", "values": [1.0, 2.0], "ok": True}, f)
    os.replace("data/b.json.tmp", "data/b.json")
    print("saved data/b.json")
"""
# What the engineer wrote after being told the earlier file "was not written".
REWRITE_BOTH = """
    import json, os
    os.makedirs("data", exist_ok=True)
    for name, item in (("a", "first"), ("b", "second")):
        path = f"data/{name}.json"
        doc = json.load(open(path)) if os.path.exists(path) else {"item": item, "ok": True}
        doc["rewritten"] = doc.get("rewritten", 0) + 1
        with open(path + ".tmp", "w") as f:
            json.dump(doc, f)
        os.replace(path + ".tmp", path)
        print("saved", path)
"""
# "If it already exists and validates, load it and skip recomputation."
LOAD_OWN = """
    import json
    doc = json.load(open("data/b.json"))
    assert doc["ok"] is True
    print("data/b.json exists and validates; loaded, recomputation skipped:", doc["item"])
"""
WRITE_OWN_THEN_FAIL = WRITE_OWN + """
    raise SystemExit(3)
"""
DELETE_EARLIER = """
    import os
    os.remove("data/a.json")
    print("removed the earlier file")
"""


def _seed(work_dir, *names):
    """Files earlier steps left in data/."""
    (work_dir / "data").mkdir(parents=True, exist_ok=True)
    for name in names:
        (work_dir / "data" / f"{name}.json").write_text('{"item": "%s", "ok": true}' % name)


class _Run:
    """One graph invoke of a step: executor, post-condition, then a scripted step
    evaluator and the arbitration `step_evaluator` runs on a rejection."""

    def __init__(self, work_dir, step, postcondition=None, number=3):
        self.state = {"step": step, "step_number": number, "work_dir": str(work_dir),
                      "attempts": 0}
        self.postcondition = postcondition or (lambda st: output_postcondition(st, _rt()))
        self.new = postcondition is None

    def execute(self, code):
        self.state["attempts"] += 1
        self.state["current_code"] = EngineerResponse(
            python_code=textwrap.dedent(code), code_explanation="x")
        self.state.update(sd_nodes.executor(self.state, _rt()))
        res = self.postcondition(self.state)
        self.state.update(res)
        return res

    def judge(self, verdict):
        self.state["current_step_verdict"] = verdict
        if not verdict.fulfilled and self.new:
            self.state.update(arbitrate_with_step_evaluator(self.state, verdict))
        return verdict


def _wrote(state, path):
    return any(f["path"] == path for f in state["data_manifest"])


def _evaluator(state):
    """The step evaluator of the measured case: rejects a run that rewrote the earlier
    file, accepts one that left it alone."""
    if _wrote(state, "data/a.json"):
        return StepVerdict(
            fulfilled=False,
            unmet_requirements=["Do not overwrite or delete `data/a.json` or any other "
                                "earlier artifacts."],
            feedback="The second item's file was saved, but the run also rewrote "
                     "`data/a.json`, which directly contradicts the requirement not to "
                     "overwrite that file.")
    return StepVerdict(fulfilled=True)


def _engineer(state, outcome):
    """The engineer of the measured case: correct first; obeys a bounce for a file "not
    written"; after the evaluator's rejection leaves the earlier file alone and, the
    step's own file being there and valid, loads it."""
    if outcome is None:
        return WRITE_OWN
    bounces = state.get("output_postcondition_bounces") or []
    if outcome == "bounced" and bounces and "data/a.json" in bounces[-1]["missing"]:
        return REWRITE_BOTH
    return LOAD_OWN


def _play(run, max_attempts=6):
    """Attempts until fulfilled or the budget is spent; one word per attempt."""
    trace, outcome = [], None
    for _ in range(max_attempts):
        res = run.execute(_engineer(run.state, outcome))
        if res["output_postcondition_rejected"]:
            outcome = "bounced"
        elif run.judge(_evaluator(run.state)).fulfilled:
            outcome = "fulfilled"
        else:
            outcome = "rejected"
        trace.append(outcome)
        if outcome == "fulfilled":
            break
    return trace


# ── (5) the replay ────────────────────────────────────────────────────────────


def test_replay_the_old_reading_alternates_until_the_attempts_run_out(tmp_path):
    """Odd attempts (correct) are bounced for not writing the protected file, even
    attempts obey and are rejected for overwriting it. From attempt 3 the bounce also
    names the step's own file, which the attempt loaded instead of rewriting."""
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3, postcondition=old_output_postcondition)
    assert _play(run) == ["bounced", "rejected"] * 3
    missing = [b["missing"] for b in run.state["output_postcondition_bounces"]]
    assert missing == [["data/a.json"],
                       ["data/b.json", "data/a.json"],
                       ["data/b.json", "data/a.json"]]


def test_replay_the_new_reading_passes_at_attempt_1(tmp_path, capsys):
    _seed(tmp_path, "a")
    before = (tmp_path / "data" / "a.json").read_text()
    run = _Run(tmp_path, STEP_3)
    assert _play(run) == ["fulfilled"]
    assert not run.state.get("output_postcondition_bounces")
    assert (tmp_path / "data" / "a.json").read_text() == before
    assert "bounced" not in capsys.readouterr().err


def test_replay_the_replanned_read_only_step(tmp_path):
    """Step 4: every attempt of the old reading that wrote nothing was bounced for three
    files; the new reading declares none and the read-only attempt passes."""
    rewrite_all = REWRITE_BOTH.replace('(("a", "first"), ("b", "second"))',
                                       '(("a", "first"), ("b", "second"), ("c", "third"))')
    assert rewrite_all != REWRITE_BOTH

    def engineer(state, outcome):
        bounced = outcome == "bounced" and state["output_postcondition_bounces"][-1]["missing"]
        return rewrite_all if bounced else LOAD_OWN

    def play(run):
        trace, outcome = [], None
        for _ in range(6):
            res = run.execute(engineer(run.state, outcome))
            outcome = ("bounced" if res["output_postcondition_rejected"]
                       else "fulfilled" if run.judge(_evaluator(run.state)).fulfilled
                       else "rejected")
            trace.append(outcome)
            if outcome == "fulfilled":
                break
        return trace

    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for d in (old_dir, new_dir):
        _seed(d, "a", "b", "c")
    old = _Run(old_dir, STEP_4, postcondition=old_output_postcondition, number=4)
    assert play(old) == ["bounced", "rejected"] * 3
    assert old.state["output_postcondition_bounces"][0]["missing"] == \
        ["data/a.json", "data/c.json", "data/b.json"]
    assert play(_Run(new_dir, STEP_4, number=4)) == ["fulfilled"]


def test_replay_an_attempt_that_loads_the_file_an_earlier_attempt_saved_passes(tmp_path, capsys):
    """Attempt 1 saves the step's file and exits 0; the step evaluator rejects it for
    another reason. Attempt 2 finds the file valid, loads it and skips the recomputation,
    as the sub-task says: it is not asked to write the file again."""
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    assert run.execute(WRITE_OWN)["output_postcondition_rejected"] is None
    run.judge(StepVerdict(fulfilled=False, feedback="Print the summary values as well."))
    res = run.execute(LOAD_OWN)
    assert not _wrote(run.state, "data/b.json")
    assert res["output_postcondition_rejected"] is None
    assert "data/b.json (attempt 1) → counted as written" in capsys.readouterr().err
    # And again on a third attempt: the record is still attempt 1's.
    assert run.execute(LOAD_OWN)["output_postcondition_rejected"] is None


def test_replay_an_attempt_that_rewrites_the_protected_file_is_bounced(tmp_path, capsys):
    """No model reads the run: the manifest says the file was there and was changed."""
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    res = run.execute(REWRITE_BOTH)
    assert res["output_postcondition_rejected"] == "protected_modified"
    assert res["execution_returncode"] == 1
    err = res["execution_stderr"]
    assert "`data/a.json` was modified by this run" in err
    assert "Do not overwrite or delete `data/a.json`" in err, "the bullet is quoted"
    assert POSTCONDITION_MARKER in err
    verdict = res["current_execution_verdict"]
    assert verdict.status == "failure" and verdict.failure_kind == "other"
    assert "data/a.json (modified)" in res["error_history"][-1]
    assert res["output_postcondition_bounces"] == [
        {"attempt": 1, "missing": [], "changed": [["data/a.json", "modified"]]}]
    assert ("protected file(s) changed by this attempt: data/a.json (modified) → bounced "
            "with no model call") in capsys.readouterr().err
    # The next attempt leaves it alone and passes; the file the bounced attempt saved
    # (it exited 0) is loaded, not rewritten.
    assert run.execute(LOAD_OWN)["output_postcondition_rejected"] is None


# ── (2) protected files ───────────────────────────────────────────────────────


def test_a_deleted_protected_file_is_bounced_and_listed_in_the_manifest_info(tmp_path):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    res = run.execute(WRITE_OWN + DELETE_EARLIER)
    assert run.state["data_manifest_info"]["deleted"] == ["data/a.json"]
    assert res["output_postcondition_rejected"] == "protected_modified"
    assert "`data/a.json` was deleted by this run" in res["execution_stderr"]
    assert "not restored" in res["execution_stderr"]
    # The file is gone, but THIS attempt did not remove it: no second bounce.
    assert run.execute(LOAD_OWN)["output_postcondition_rejected"] is None
    assert run.state["data_manifest_info"]["deleted"] == []


def test_creating_a_protected_path_is_left_to_the_step_evaluator(tmp_path):
    """"Do not overwrite" a file that is not there: nothing was overwritten."""
    run = _Run(tmp_path, STEP_3)          # data/a.json does not exist
    res = run.execute(REWRITE_BOTH)
    assert [f["change"] for f in run.state["data_manifest"]
            if f["path"] == "data/a.json"] == ["new"]
    assert res["output_postcondition_rejected"] is None


def test_a_missing_output_and_a_changed_protected_file_are_reported_together(tmp_path):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    res = run.execute("""
        import json
        json.dump({"item": "first", "ok": True, "extra": 1}, open("data/a.json", "w"))
        print("updated data/a.json")
    """)
    assert res["output_postcondition_rejected"] == "missing_output+protected_modified"
    err = res["execution_stderr"]
    assert "`data/b.json` (not on disk)" in err and "`data/a.json` was modified" in err
    assert res["output_postcondition_bounces"][-1] == {
        "attempt": 1, "missing": ["data/b.json"], "changed": [["data/a.json", "modified"]]}


def test_changed_protected_paths_never_answers_blind():
    step = _step("x", "Do not overwrite `data/a.json`.")
    modified = [{"path": "data/a.json", "bytes": 1, "change": "modified"}]
    assert changed_protected_paths(step, modified, {}) == [("data/a.json", "modified")]
    assert changed_protected_paths(step, [], {"deleted": ["data/a.json"]}) == \
        [("data/a.json", "deleted")]
    assert changed_protected_paths(step, None, None) == []                  # no manifest
    assert changed_protected_paths(step, [{"path": "data/a.json"}], None) == []   # no change kind
    assert changed_protected_paths(step, [{"path": "a.json", "change": "modified"}], {}) == [], \
        "a same-named file elsewhere is not the protected one"
    assert changed_protected_paths(step, modified, {}, waived=["data/a.json"]) == []


# ── (3) a declared output an earlier attempt wrote ────────────────────────────


def test_a_stale_file_from_before_the_step_never_counts(tmp_path):
    """The original protection: the declared file is on disk, left by an earlier run of
    the whole task, and no attempt of this step wrote it. Every attempt that only loads
    it is bounced, the first and the later ones alike."""
    _seed(tmp_path, "a", "b")
    run = _Run(tmp_path, STEP_3)
    for _ in range(3):
        res = run.execute(LOAD_OWN)
        assert res["output_postcondition_rejected"] == "missing_output"
        assert "`data/b.json` (on disk, but not written by this attempt or by an earlier " \
               "attempt of this step)" in res["execution_stderr"]
        assert "stale copy that was on disk before this step began does not count" in \
            res["execution_stderr"]
    # Written by this step, it passes, and from then on a load does too.
    assert run.execute(WRITE_OWN)["output_postcondition_rejected"] is None
    assert run.execute(LOAD_OWN)["output_postcondition_rejected"] is None


def test_a_file_an_earlier_run_of_the_same_step_wrote_does_not_count(tmp_path):
    """The same plan run again in the same directory (or a step restarted after a kill):
    the history file is found and the old run's write is on record, but it is not this
    run's."""
    _seed(tmp_path, "a")
    first = _Run(tmp_path, STEP_3)
    assert first.execute(WRITE_OWN)["output_postcondition_rejected"] is None
    again = _Run(tmp_path, STEP_3)                # a fresh graph invoke of the same step
    res = again.execute(LOAD_OWN)
    assert again.state["step_history"]["run"] == 2
    assert res["output_postcondition_rejected"] == "missing_output"
    assert "written by an earlier run of this step" in res["execution_stderr"]


def test_a_file_a_failed_attempt_wrote_does_not_count(tmp_path):
    """Attempt 1 wrote the file and then exited non-zero: the file may be half of what
    it meant to write."""
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    res = run.execute(WRITE_OWN_THEN_FAIL)
    assert run.state["execution_returncode"] == 3
    assert res["output_postcondition_rejected"] is None      # a crash is left alone
    res = run.execute(LOAD_OWN)
    assert res["output_postcondition_rejected"] == "missing_output"
    assert "saved by attempt 1, whose script did not exit 0" in res["execution_stderr"]


def test_a_file_changed_since_the_attempt_that_wrote_it_does_not_count(tmp_path):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    assert run.execute(WRITE_OWN)["output_postcondition_rejected"] is None
    target = tmp_path / "data" / "b.json"
    target.write_text(target.read_text() + " ")           # changed by something else
    res = run.execute(LOAD_OWN)
    assert res["output_postcondition_rejected"] == "missing_output"
    assert "changed after attempt 1 saved it" in res["execution_stderr"]


def test_the_history_records_whether_each_writing_attempt_exited_0(tmp_path):
    import json

    _seed(tmp_path, "a")
    run = _Run(tmp_path, STEP_3)
    run.execute(WRITE_OWN_THEN_FAIL)
    writes = json.loads((tmp_path / "logs" / "step_3_history.json").read_text())["writes"]
    assert writes["data/b.json"][:2] == [1, 1] and writes["data/b.json"][3] is False
    run.execute(WRITE_OWN)
    writes = json.loads((tmp_path / "logs" / "step_3_history.json").read_text())["writes"]
    assert writes["data/b.json"][:2] == [1, 2] and writes["data/b.json"][3] is True
    # A history written before the field existed: no attempt is known to be clean.
    old = am.StepHistory(writes={"data/b.json": [1, 1, [1, 2, 3, 4]]}, attempt=2, run=1)
    assert old.reusable("data/b.json", [1, 2, 3, 4]) is None
    new = am.StepHistory(writes={"data/b.json": [1, 1, [1, 2, 3, 4], True]}, attempt=2, run=1)
    assert new.reusable("data/b.json", [1, 2, 3, 4]) == 1
    assert new.reusable("data/b.json", [1, 2, 3, 5]) is None      # changed since
    assert new.reusable("data/b.json", None) is None              # gone
    assert new.reusable("data/a.json", [1, 2, 3, 4]) is None      # no record: pre-existing
    later_run = am.StepHistory(writes={"data/b.json": [1, 1, [1, 2, 3, 4], True]},
                               attempt=1, run=2)
    assert later_run.reusable("data/b.json", [1, 2, 3, 4]) is None


# ── (4) a disagreement with the step evaluator ────────────────────────────────

# Wording the patterns misread: "alongside" is not a list separator, so the save verb
# reaches the second path and both are declared.
MISREAD = _step(
    "Compute the summary table for the second item.",
    "Save `data/b.json` alongside `data/a.json`, which this step only reads.")
ONLY_READS = StepVerdict(
    fulfilled=False,
    unmet_requirements=["`data/a.json` is only read by this step."],
    feedback="The second item's file is correct. The run also rewrote `data/a.json`, "
             "which contradicts the sub-task: this step only reads it.")


def test_the_evaluators_objection_is_read_from_the_verdict():
    path = "data/a.json"
    # An unmet requirement that protects the path.
    assert evaluator_objects_to_writing(
        path, ["Do not overwrite or delete `data/a.json` or any other earlier artifacts."], "")
    assert evaluator_objects_to_writing(path, [TOUCH_NOTHING], "")
    # Feedback: the run wrote it, and that breaks the sub-task.
    assert evaluator_objects_to_writing(path, ONLY_READS.unmet_requirements, ONLY_READS.feedback)
    assert evaluator_objects_to_writing(
        path, [], "`data/a.json` was overwritten although the sub-task forbids it.")
    assert evaluator_objects_to_writing(path, [], "Leave `data/a.json` untouched.")
    # Not objections to the write: a comment on its content, another file's violation,
    # a mention with no judgement.
    assert not evaluator_objects_to_writing(
        path, [], "`data/a.json` was saved but its values are not finite; fix the mean.")
    assert not evaluator_objects_to_writing(
        path, ["Do not overwrite `data/c.json`."],
        "The run saved `data/a.json` and also overwrote `data/c.json`, which violates the "
        "sub-task.")
    assert not evaluator_objects_to_writing(path, ["Print the values of `data/a.json`."], "")
    # The reverse reading: the verdict wants the path written.
    assert evaluator_requires_writing(path, ["Write the corrected table to `data/a.json`."], "")
    assert evaluator_requires_writing(path, [], "`data/a.json` was not updated by this run.")
    assert evaluator_requires_writing(path, [], "The run did not write `data/a.json`.")
    assert evaluator_requires_writing(path, [], "`data/a.json` is still missing.")
    assert not evaluator_requires_writing(path, [], "`data/a.json` was read correctly.")
    # A prohibition repeated in the feedback is not a request to write, and a word such
    # as "missing" about something else in the sentence is not about the path.
    assert not evaluator_requires_writing(
        path, [], "For the next attempt, do not write or modify `data/a.json`.")
    assert not evaluator_requires_writing(
        path, [], "`data/a.json` was left as it was, but the printed values are missing.")


def test_a_verdict_that_reads_both_ways_about_a_path_settles_nothing(tmp_path):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, MISREAD)
    run.execute(REWRITE_BOTH)
    run.judge(StepVerdict(
        fulfilled=False,
        unmet_requirements=["Do not overwrite `data/a.json`.",
                            "Write the corrected table to `data/a.json`."],
        feedback="The requirements about `data/a.json` cannot both be met."))
    assert "output_postcondition_waived" not in run.state


def test_bounce_then_rejection_the_evaluators_reading_stands(tmp_path, capsys):
    """Attempt 1 is bounced for not writing a path, attempt 2 writes it and is rejected
    for that. The path is dropped for the rest of the step, the engineer is told both
    messages, and attempt 3 is not bounced again."""
    _seed(tmp_path, "a")
    assert declared_output_paths(MISREAD) == ["data/b.json", "data/a.json"]
    run = _Run(tmp_path, MISREAD)
    assert run.execute(WRITE_OWN)["output_postcondition_rejected"] == "missing_output"
    assert run.execute(REWRITE_BOTH)["output_postcondition_rejected"] is None
    capsys.readouterr()
    run.judge(ONLY_READS)

    assert run.state["output_postcondition_waived"] == {
        "declared": ["data/a.json"], "protected": []}
    log = capsys.readouterr().err
    assert CONFLICT_LOG.format("data/a.json") in log
    note = run.state["output_conflict_note"]
    assert "The step evaluator's reading stands" in note
    assert "sent attempt 1 back for NOT writing `data/a.json`" in note          # message 1
    assert "rejected attempt 2, which wrote it" in note                           # message 2
    assert "which contradicts the sub-task: this step only reads it" in note      # its text
    assert "no longer requires `data/a.json`" in note
    # The engineer's next prompt carries it, in the mode a rejection selects.
    block = render_retry_context(
        attempts=3, max_attempts=6, last_code="pass", last_stderr=None, last_stdout="x",
        last_returncode=None, last_timed_out=None, last_fix_suggestion=None,
        error_history=run.state["error_history"],
        last_step_unmet=ONLY_READS.unmet_requirements, last_step_feedback=ONLY_READS.feedback,
        output_conflict_note=note)
    assert note in block and block.index(note) < block.index("**Unmet requirements:**")

    # Attempt 3 leaves the earlier file alone and loads its own: no bounce.
    res = run.execute(LOAD_OWN)
    assert res["output_postcondition_rejected"] is None
    assert len(run.state["output_postcondition_bounces"]) == 1


def test_rejection_first_the_bounce_never_happens(tmp_path, capsys):
    """The other order: attempt 1 writes the path and is rejected for it; attempt 2
    leaves it alone. Without the waiver it would now be bounced for not writing it."""
    _seed(tmp_path, "a")
    run = _Run(tmp_path, MISREAD)
    assert run.execute(REWRITE_BOTH)["output_postcondition_rejected"] is None
    run.judge(ONLY_READS)
    assert run.state["output_postcondition_waived"]["declared"] == ["data/a.json"]
    assert CONFLICT_LOG.format("data/a.json") in capsys.readouterr().err
    assert not run.state.get("output_conflict_note"), \
        "the engineer has had one message only, so there is nothing to reconcile"
    assert run.execute(LOAD_OWN)["output_postcondition_rejected"] is None
    # The waiver on its own, with no history to lean on (the copy attempt 1 saved would
    # count anyway while it is unchanged): the path is simply no longer asked for.
    st = run.state
    args = (MISREAD, st["work_dir"], st["data_manifest"], st["data_manifest_info"])
    assert op.missing_declared_outputs(*args) == ["data/b.json", "data/a.json"]
    assert op.missing_declared_outputs(*args, waived=["data/a.json"]) == ["data/b.json"]


def test_a_rejection_for_another_reason_changes_nothing(tmp_path):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, MISREAD)
    run.execute(REWRITE_BOTH)
    run.judge(StepVerdict(
        fulfilled=False, unmet_requirements=["Print the summary values."],
        feedback="`data/a.json` and `data/b.json` were saved, but no value was printed."))
    assert "output_postcondition_waived" not in run.state
    assert "output_conflict_note" not in run.state


def test_a_protected_path_the_evaluator_wants_written_loses_its_protection(tmp_path, capsys):
    """The reverse disagreement: the text reads as a prohibition, an attempt is bounced
    for changing the file, the next leaves it alone and the step evaluator rejects it
    because the file was not updated."""
    _seed(tmp_path, "a")
    step = _step("Extend the table of the first item.",
                 "Never rewrite `data/a.json`: add the new rows to it.")
    assert protected_paths(step) == ["data/a.json"]
    run = _Run(tmp_path, step)
    update = """
        import json
        doc = json.load(open("data/a.json"))
        doc["rows"] = doc.get("rows", 0) + 2
        json.dump(doc, open("data/a.json", "w"))
        print("added 2 rows to data/a.json")
    """
    assert run.execute(update)["output_postcondition_rejected"] == "protected_modified"
    assert run.execute("print('left data/a.json as it was')")["output_postcondition_rejected"] is None
    capsys.readouterr()
    run.judge(StepVerdict(
        fulfilled=False, unmet_requirements=["Add the new rows to the table."],
        feedback="`data/a.json` was not updated by this run, so the new rows are missing."))
    assert run.state["output_postcondition_waived"] == {
        "declared": [], "protected": ["data/a.json"]}
    assert CONFLICT_LOG.format("data/a.json") in capsys.readouterr().err
    note = run.state["output_conflict_note"]
    assert "sent attempt 1 back for changing `data/a.json`" in note
    assert "no longer treats `data/a.json` as a file to leave unchanged" in note
    assert run.execute(update)["output_postcondition_rejected"] is None


def test_the_arbitration_stands_down_with_the_check_off_or_remote(tmp_path, monkeypatch):
    _seed(tmp_path, "a")
    run = _Run(tmp_path, MISREAD)
    run.execute(REWRITE_BOTH)
    off = PlanContext(main_task="t", check_declared_outputs=False)
    assert arbitrate_with_step_evaluator(run.state, ONLY_READS, off) == {}
    monkeypatch.setenv(REMOTE_ENV, "host:1234")
    assert arbitrate_with_step_evaluator(run.state, ONLY_READS, PlanContext(main_task="t")) == {}
    monkeypatch.delenv(REMOTE_ENV)
    assert arbitrate_with_step_evaluator({**run.state, "data_manifest": None}, ONLY_READS) == {}
    assert arbitrate_with_step_evaluator(run.state, ONLY_READS)["output_postcondition_waived"]


# ── the whole graph ───────────────────────────────────────────────────────────


def _graph_run(tmp_path, monkeypatch, step, choose_code, step_verdict, max_n_attempts=6):
    """The compiled self_debug graph with scripted models: `choose_code(prompt, k)` is
    the engineer, `step_verdict(k)` the step evaluator's answer for its k-th call."""
    import subprocess

    from cmbagent_lg.self_debug.graph import graph

    calls = types.SimpleNamespace(prompts=[], execution=0, step=0, runs=0)

    class _Proposer:
        def invoke(self, msgs, config=None):
            calls.prompts.append(msgs[0].content)
            return types.SimpleNamespace(text=textwrap.dedent(
                choose_code(msgs[0].content, len(calls.prompts))))

    class _Formatter:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, msgs, config=None):
            return EngineerResponse(python_code=msgs[1].content, code_explanation="x")

    class _Critic:
        schema = None

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, msgs, config=None):
            if self.schema is ExecutionVerdict:
                calls.execution += 1
                return ExecutionVerdict(status="success")
            calls.step += 1
            return step_verdict(calls.step)

    critic = _Critic()
    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: critic)
    real_popen = subprocess.Popen

    def _counting_popen(*a, **k):
        calls.runs += 1
        return real_popen(*a, **k)

    monkeypatch.setattr(sd_nodes.subprocess, "Popen", _counting_popen)
    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False,
                      vlm_enabled=False, max_n_attempts=max_n_attempts)
    out = graph.invoke({"step": step, "step_number": 3, "work_dir": str(tmp_path)},
                       context=ctx)
    return out, calls


def test_graph_the_measured_step_passes_at_attempt_1(tmp_path, monkeypatch):
    _seed(tmp_path, "a")
    out, calls = _graph_run(tmp_path, monkeypatch, STEP_3,
                            lambda prompt, k: WRITE_OWN,
                            lambda k: StepVerdict(fulfilled=True))
    assert out["current_step_verdict"].fulfilled
    assert (calls.runs, calls.execution, calls.step) == (1, 1, 1)
    assert out["attempts"] == 1 and not out.get("output_postcondition_bounces")


def test_graph_a_rewrite_of_the_protected_file_costs_no_model_call(tmp_path, monkeypatch):
    _seed(tmp_path, "a")
    out, calls = _graph_run(
        tmp_path, monkeypatch, STEP_3,
        lambda prompt, k: REWRITE_BOTH if k == 1 else LOAD_OWN,
        lambda k: StepVerdict(fulfilled=True))
    assert out["current_step_verdict"].fulfilled and out["attempts"] == 2
    # Two runs, but each evaluator was called once: for the second attempt only.
    assert (calls.runs, calls.execution, calls.step) == (2, 1, 1)
    assert "`data/a.json` was modified by this run" in calls.prompts[1]
    assert "Do not overwrite or delete `data/a.json`" in calls.prompts[1]
    assert (tmp_path / "codebase" / "step_3_failure_1.py").exists()


def test_graph_a_disagreement_is_settled_and_the_step_finishes(tmp_path, monkeypatch, capsys):
    """Bounce, obey, rejection, then the note: the third attempt passes instead of the
    loop running to the cap."""
    _seed(tmp_path, "a")

    def choose_code(prompt, k):
        if "The step evaluator's reading stands" in prompt:
            return LOAD_OWN
        if "were not written by this run" in prompt:
            return REWRITE_BOTH
        return WRITE_OWN

    out, calls = _graph_run(
        tmp_path, monkeypatch, MISREAD, choose_code,
        lambda k: ONLY_READS if k == 1 else StepVerdict(fulfilled=True))
    assert out["current_step_verdict"].fulfilled
    assert out["attempts"] == 3 and calls.runs == 3
    assert calls.step == 2, "attempt 1 was bounced without a model call"
    third = calls.prompts[2]
    assert "sent attempt 1 back for NOT writing `data/a.json`" in third
    assert "rejected attempt 2, which wrote it" in third
    assert "this step only reads it" in third
    assert out["output_postcondition_waived"]["declared"] == ["data/a.json"]
    assert CONFLICT_LOG.format("data/a.json") in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
