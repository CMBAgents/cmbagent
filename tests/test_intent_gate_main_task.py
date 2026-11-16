"""Intent gate: the provided-input scan must honour the RUN-LEVEL text, and a failed
attempt is terminal only on a genuine file-not-found.

Measured on the qm9-joint fleet (465 iterations): 231 halted at step 1 with
`logs/step_1_missing_input.json`. Denario passes its data description as
`PlanContext.main_task` — "The file /scratch/.../qm9-joint/data/qm9.csv contains the
QM9 dataset …" — while the planner's bullet says only "Load the `qm9.csv` dataset."
Pre-flight scanned the STEP text alone, found no existing path, saw `data/` empty,
armed the gate, and the engineer's successful load from the absolute path was then
called fabrication (184 clean runs) or its first honest ValueError was made terminal
(47). Both are fixed here:

  (a) `named_input_for_step` scans `ctx.main_task` + `ctx.engineer_append_instructions`
      as well as the step's sub_task + bullets;
  (b) "qm9.csv from /abs/dir/" and a bare basename that lives under a directory the
      text names both resolve;
  (c) `_intent_gate_unfulfillable` is terminal ONLY for a genuine file-not-found on an
      input the script does not itself write, never for an arbitrary exception;
  (d) `enable_intent_gate` is a real `PlanContext` field a caller can pass.

The real fabrication case — `data/` empty, no input named anywhere, script invents
data — stays terminal, and so does a NAMED-but-ABSENT input.
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug import preflight as pf
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, IntentVerdict


def _code(python_code: str) -> EngineerResponse:
    return EngineerResponse(python_code=python_code, code_explanation="test")

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"

# Verbatim from the fleet's input_files/data_description.md (Project0/Iteration3), with
# the absolute path re-rooted onto the test's tmp dir.
DATA_DESCRIPTION = (
    "The file {abs_csv} contains the QM9 dataset: 133,885 small organic molecules, each "
    "computed with density functional theory at the B3LYP/6-31G(2df,p) level. Each row is "
    "one molecule, and the columns record its SMILES string, its rotational constants, and "
    "twelve computed electronic, energetic and thermodynamic properties. This CSV is the "
    "whole dataset; read it with pandas. There are no 3D coordinates, no partial charges "
    "and no InChI strings anywhere in this project, and nothing is to be downloaded.\n\n"
    "## The research question\n\nProperties may instead be organised into groups …"
)
# Denario's _DATA_DIR_CONVENTION, appended to engineer_append_instructions.
DATA_DIR_CONVENTION = (
    "\n\nOUTPUT-FILE CONVENTION (IMPORTANT): write ALL generated artifacts — "
    "plots/figures, tables, metrics, and data files — directly into the `data/` "
    "directory (e.g. `data/fit.png`, `data/metrics.txt`), creating it if needed."
)

# Step-1 wordings measured on the halted iterations, one per naming category.
STEP_BASENAME = Step(  # Project0/Iteration3 — "qm9.csv-basename"
    sub_task="Data Preprocessing and Outlier Identification",
    sub_task_agent="engineer",
    bullet_points=[
        "Load the `qm9.csv` dataset.",
        "Deduplicate rows based on the SMILES string (keep the first occurrence).",
        'Create and save two datasets to `data/`: "Full" and "Cleaned".',
    ],
)
STEP_NOUN = Step(  # Project0/Iteration4 — "qm9-noun": no file name at all
    sub_task="Data Preprocessing and Outlier Structural Profiling",
    sub_task_agent="engineer",
    bullet_points=[
        "Load the QM9 dataset and deduplicate based on SMILES strings (retain first occurrence).",
        "Apply a log(A + 1) transformation to the rotational constant A.",
    ],
)
STEP_RELATIVE = Step(  # Project0/Iteration0 — "data/qm9.csv": a relative path that does not exist
    sub_task="Data Preprocessing, Redundancy Elimination, and Structural Parsing",
    sub_task_agent="engineer",
    bullet_points=["Load QM9 from data/qm9.csv and deduplicate based on SMILES strings."],
)


def _step_from_dir(abs_dir: str) -> Step:  # Project3/Iteration4 — "other-abs-path"
    return Step(
        sub_task="Data Ingestion, Structural Parsing, and Family Categorization",
        sub_task_agent="engineer",
        bullet_points=[
            "Silence RDKit logger (RDLogger.DisableLog('rdApp.*')).",
            f"Load qm9.csv from {abs_dir}/ and deduplicate based on the SMILES string.",
        ],
    )


STEP_GENERATE = Step(  # a genuine generation step — the gate must stay out of its way
    sub_task="Generate a synthetic dataset",
    sub_task_agent="engineer",
    bullet_points=["Draw 1000 samples from a 2-component Gaussian mixture and save to data/mix.csv."],
)


@pytest.fixture
def fleet(tmp_path):
    """A fleet-shaped layout: the dataset OUTSIDE the work_dir, work_dir/data EMPTY."""
    data_root = tmp_path / "fleets" / "qm9-joint" / "data"
    data_root.mkdir(parents=True)
    csv = data_root / "qm9.csv"
    csv.write_text("smiles,A,B,C,mu,alpha,homo,lumo,gap\nC,157.7,157.7,157.7,0.0,13.2,-0.39,0.12,0.51\n")
    work_dir = tmp_path / "Project0" / "Iteration3" / "experiment_output"
    (work_dir / "data").mkdir(parents=True)
    return types.SimpleNamespace(csv=csv, data_root=data_root, work_dir=work_dir)


def _ctx(fleet, **kw):
    kw.setdefault("main_task", DATA_DESCRIPTION.format(abs_csv=fleet.csv))
    kw.setdefault("engineer_append_instructions", DATA_DIR_CONVENTION)
    return PlanContext(**kw)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(fleet, step, **kw):
    return {"step": step, "work_dir": str(fleet.work_dir), "step_number": 1, "attempts": 1, **kw}


class _Classifier:
    """The gate's one model call, stubbed and counted — 'never asked' is provable."""

    def __init__(self, needs_provided_input=True):
        self.verdict = IntentVerdict(needs_provided_input=needs_provided_input,
                                     reason="The instruction explicitly states to load the qm9.csv dataset.")
        self.calls = 0

    def __enter__(self):
        self.saved = pf._classify_intent
        me = self

        def _classify(ctx, task):
            me.calls += 1
            return me.verdict

        pf._classify_intent = _classify
        return self

    def __exit__(self, *exc):
        pf._classify_intent = self.saved


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch):
    monkeypatch.delenv(REMOTE_ENV, raising=False)


# ── (a) the run-level text is scanned ───────────────────────────────────────


def test_step_only_scan_is_the_premise(fleet):
    """The premise: none of the halted wordings names an existing path on their own."""
    for step in (STEP_BASENAME, STEP_NOUN, STEP_RELATIVE):
        assert pf.resolve_named_input(pf._task_text(step), fleet.work_dir) is None


@pytest.mark.parametrize("step", [STEP_BASENAME, STEP_NOUN, STEP_RELATIVE],
                         ids=["basename", "noun", "relative-data/"])
def test_main_task_absolute_path_resolves_for_every_halted_wording(fleet, step):
    found = pf.named_input_for_step(step, fleet.work_dir, _ctx(fleet))
    assert found == fleet.csv, found


def test_engineer_append_instructions_are_scanned_too(fleet):
    ctx = _ctx(fleet, main_task="Study the QM9 molecules.",
               engineer_append_instructions=f"Always read the dataset from {fleet.csv}.")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) == fleet.csv


def test_preflight_does_not_arm_the_gate_and_never_asks_the_classifier(fleet):
    """THE BUG, end to end at the node: data/ empty + absolute path in main_task →
    `input_dir_empty` False, no classifier call, no directive facts."""
    ctx = _ctx(fleet)
    with _Classifier() as c:
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(ctx))
    assert out.get("input_dir_empty") is False, out
    assert out.get("needs_provided_input") is None, out
    assert c.calls == 0, "the classifier was asked although the input exists"
    assert sd_nodes._intent_gate_flagged({**_state(fleet, STEP_BASENAME), **out}, ctx) is False
    # The engineer is told WHERE the file is, from the real bytes.
    assert str(fleet.csv) in out["preflight_report"], out["preflight_report"]
    assert "smiles" in out["preflight_report"]


def test_the_clean_load_is_no_longer_called_fabrication(fleet):
    """End of the chain (184 iterations): the successful load routes to the evaluators."""
    ctx = _ctx(fleet)
    with _Classifier():
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(ctx))
    state = {
        **_state(fleet, STEP_BASENAME), **out,
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "data_manifest": [{"path": "data/qm9_full.csv", "bytes": 512}],
        "execution_stdout": "Full: (133885, 12)  Cleaned: (127190, 12)\n",
    }
    assert sd_nodes._intent_gate_fabrication(state, ctx) is False
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) != "missing_input"


# ── (b) "X.csv from /abs/dir/" and basename-under-named-dir ────────────────


def test_basename_from_directory_in_step_text(fleet):
    step = _step_from_dir(str(fleet.data_root))
    assert pf.resolve_named_input(pf._task_text(step), fleet.work_dir) == fleet.csv


def test_bare_basename_under_directory_named_in_run_text(fleet):
    ctx = _ctx(fleet, main_task=f"All inputs live under {fleet.data_root}/ — nothing is to be downloaded.")
    assert pf.named_input_for_step(STEP_BASENAME, fleet.work_dir, ctx) == fleet.csv


def test_directory_without_trailing_slash_and_tilde(fleet, monkeypatch):
    ctx = _ctx(fleet, main_task=f"The dataset directory is {fleet.data_root} (read qm9.csv with pandas).")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) == fleet.csv
    monkeypatch.setenv("HOME", str(fleet.data_root.parent))
    ctx = _ctx(fleet, main_task="Load ~/data/qm9.csv with pandas.")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) == fleet.csv


def test_named_but_absent_stays_unresolved(fleet):
    """A NAMED-but-ABSENT path is exactly the fabrication case: it must not resolve."""
    ctx = _ctx(fleet, main_task=f"The file {fleet.data_root}/does_not_exist.csv contains the data.")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) is None
    # …but a basename the STEP names that does live in that file's directory is a
    # provided input (the directory was named, the file is real): resolves.
    assert pf.named_input_for_step(STEP_BASENAME, fleet.work_dir, ctx) == fleet.csv
    ctx = _ctx(fleet, main_task=f"Load nope.csv from {fleet.data_root}/.")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) is None
    # an output-only mention does not count either (does not exist yet)
    ctx = _ctx(fleet, main_task="Write the metrics to data/metrics.json when done.")
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, ctx) is None


def test_scan_never_raises_on_hostile_text(fleet):
    hostile = ("~nosuchuser/x.csv " + "/" * 300 + " ../../etc/passwd.txt \x00.csv "
               + "a" * 20000 + ".csv " + " ".join(f"/d{i}/f{i}.csv" for i in range(500)))
    assert pf.resolve_named_input(hostile, fleet.work_dir) is None
    assert pf.resolve_named_input(hostile, None) is None
    assert pf.resolve_named_input("", fleet.work_dir) is None
    assert pf.named_input_for_step(None, None, None) is None
    assert pf.named_input_for_step(STEP_NOUN, fleet.work_dir, object()) is None


# ── the resolved path is ABSOLUTE, never the engine's cwd ──────────────────
#
# Production: the fleet worker's cwd is the fleet dir, which holds data/qm9.csv, while
# each iteration's work_dir/data is empty. The executor runs the script with cwd =
# work_dir, so a cwd-relative hit is unreachable to the script — and the pre-flight
# report says "load it from this exact path". 16/467 real step-1 wordings say
# "Load QM9 from data/qm9.csv"; they used to resolve to the cwd-relative file BEFORE the
# absolute path in main_task was tried, a guaranteed FileNotFoundError on attempt 1.


@pytest.fixture
def fleet_cwd(fleet, monkeypatch):
    """chdir into a dir that contains data/<basename> — the production cwd shape."""
    cwd = fleet.data_root.parent  # …/fleets/qm9-joint, whose data/qm9.csv is the dataset
    assert (cwd / "data" / "qm9.csv").is_file()
    monkeypatch.chdir(cwd)
    return cwd


def test_relative_token_resolves_to_the_absolute_input_not_the_cwd(fleet, fleet_cwd):
    found = pf.named_input_for_step(STEP_RELATIVE, fleet.work_dir, _ctx(fleet))
    assert found is not None and found.is_absolute(), found
    assert found == fleet.csv


def test_cwd_relative_existence_alone_never_resolves(fleet, fleet_cwd):
    """With NOTHING named in the run text, `data/qm9.csv` existing under the engine's cwd
    is not a provided input: the script cannot reach it."""
    assert pf.resolve_named_input("Load QM9 from data/qm9.csv.", fleet.work_dir) is None
    assert pf.resolve_named_input("Load QM9 from data/qm9.csv.", None) is None
    # …but under work_dir it is the ordinary case, and it comes back absolute
    (fleet.work_dir / "data" / "qm9.csv").write_text("smiles\nC\n")
    found = pf.resolve_named_input("Load QM9 from data/qm9.csv.", fleet.work_dir)
    assert found == fleet.work_dir / "data" / "qm9.csv" and found.is_absolute()


def test_preflight_report_hands_the_engineer_the_absolute_path(fleet, fleet_cwd):
    ctx = _ctx(fleet)
    with _Classifier() as c:
        out = pf.preflight(_state(fleet, STEP_RELATIVE), _rt(ctx))
    assert out.get("input_dir_empty") is False and c.calls == 0
    first = out["preflight_report"].splitlines()[0]
    assert f"found OUTSIDE data/ at: {fleet.csv}" in first, first
    assert "at: data/qm9.csv" not in first


def test_relative_work_dir_still_yields_an_absolute_path(fleet, monkeypatch):
    monkeypatch.chdir(fleet.work_dir.parent)
    (fleet.work_dir / "data" / "qm9.csv").write_text("smiles\nC\n")
    found = pf.resolve_named_input("Load data/qm9.csv.", Path(fleet.work_dir.name))
    assert found is not None and found.is_absolute(), found
    assert found == fleet.work_dir / "data" / "qm9.csv"


# ── the real fabrication case is still terminal ────────────────────────────


def test_real_fabrication_case_still_terminal(fleet):
    """data/ empty, nothing named anywhere, script invents data and 'succeeds'."""
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    with _Classifier() as c:
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(ctx))
    assert out.get("input_dir_empty") is True and out.get("needs_provided_input") is True, out
    assert c.calls == 1
    state = {
        **_state(fleet, STEP_BASENAME), **out,
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "data_manifest": [{"path": "data/qm9_full.csv", "bytes": 512}],
        "execution_stdout": "(1000, 12)\n",
    }
    assert sd_nodes._intent_gate_fabrication(state, ctx) is True
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "missing_input"


def test_generation_step_is_untouched(fleet):
    ctx = _ctx(fleet, main_task="Build a toy pipeline.", engineer_append_instructions="")
    with _Classifier(needs_provided_input=False) as c:
        out = pf.preflight(_state(fleet, STEP_GENERATE), _rt(ctx))
    assert out.get("input_dir_empty") is True and c.calls == 1
    assert sd_nodes._intent_gate_flagged({**_state(fleet, STEP_GENERATE), **out}, ctx) is False


def test_populated_data_dir_costs_nothing(fleet):
    (fleet.work_dir / "data" / "provided.csv").write_text("a,b\n1,2\n")
    with _Classifier() as c:
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(_ctx(fleet)))
    assert out.get("input_dir_empty") is False and c.calls == 0
    assert "provided.csv" in out["preflight_report"]


# ── (c) a failed attempt is terminal only on a genuine file-not-found ──────


def _armed(fleet, ctx):
    with _Classifier():
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(ctx))
    assert out["input_dir_empty"] is True and out["needs_provided_input"] is True
    return out


def _failed(fleet, armed, stderr, code, kind="other"):
    return {
        **_state(fleet, STEP_BASENAME), **armed,
        "current_execution_verdict": ExecutionVerdict(status="failure", failure_kind=kind,
                                                      error_summary=stderr.splitlines()[-1]),
        "execution_stderr": stderr, "execution_stdout": "",
        "current_code": _code(code),
    }


@pytest.mark.parametrize("stderr", [
    "Traceback (most recent call last):\n  File \"s.py\", line 9\nValueError: could not convert string to float: 'C'",
    "Traceback (most recent call last):\nKeyError: 'smiles'",
    "Traceback (most recent call last):\npandas.errors.ParserError: Error tokenizing data. C error: Expected 5 fields",
    "ZeroDivisionError: division by zero",
    "Missing input; aborting.\nSystemExit: 1",
], ids=["ValueError", "KeyError", "ParserError", "ZeroDivisionError", "sys.exit"])
def test_arbitrary_failure_is_a_retry_not_terminal(fleet, stderr):
    """47 iterations: the first honest non-FNF failure ended the step at attempt 1."""
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    state = _failed(fleet, _armed(fleet, ctx), stderr, "import pandas as pd\ndf = pd.read_csv('data/qm9.csv')\n")
    assert sd_nodes._intent_gate_unfulfillable(state, ctx) is False
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "engineer"


def test_genuine_file_not_found_is_still_terminal(fleet):
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    stderr = "Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: 'data/qm9.csv'"
    state = _failed(fleet, _armed(fleet, ctx), stderr, "import pandas as pd\ndf = pd.read_csv('data/qm9.csv')\n")
    assert sd_nodes._intent_gate_unfulfillable(state, ctx) is True
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "missing_input"
    # unparseable path, honest `raise FileNotFoundError(...)` — still terminal
    stderr2 = "Traceback (most recent call last):\nFileNotFoundError: qm9.csv is absent from data/"
    state2 = _failed(fleet, _armed(fleet, ctx), stderr2, "import os, sys\nraise FileNotFoundError('qm9.csv is absent from data/')\n")
    assert sd_nodes._intent_gate_unfulfillable(state2, ctx) is True


def test_create_then_read_file_not_found_is_not_terminal(fleet):
    """The script writes the file it then fails to read: a bug, not a missing input."""
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    stderr = "FileNotFoundError: [Errno 2] No such file or directory: 'data/tmp.csv'"
    code = "df.to_csv('data/tmp.csv')\npd.read_csv('data/tmp.csv')\n"
    state = _failed(fleet, _armed(fleet, ctx), stderr, code)
    assert sd_nodes._intent_gate_unfulfillable(state, ctx) is False


def test_package_fault_is_never_terminal(fleet):
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    stderr = "ModuleNotFoundError: No module named 'rdkit'"
    state = _failed(fleet, _armed(fleet, ctx), stderr, "from rdkit import Chem\n", kind="missing_module")
    assert sd_nodes._intent_gate_unfulfillable(state, ctx) is False
    state["current_execution_verdict"] = ExecutionVerdict(status="failure", failure_kind="other",
                                                          error_summary="no such file 'rdkit.so' ModuleNotFoundError")
    state["execution_stderr"] = "ImportError: no such file 'rdkit.so'\nModuleNotFoundError: No module named 'rdkit'"
    assert sd_nodes._intent_gate_unfulfillable(state, ctx) is False


# ── (d) enable_intent_gate is a real PlanContext field ─────────────────────


def test_enable_intent_gate_is_a_dataclass_field_denario_can_pass():
    import dataclasses
    names = {f.name for f in dataclasses.fields(PlanContext)}
    assert "enable_intent_gate" in names and "intent_gate_model" in names
    assert PlanContext(main_task="t").enable_intent_gate is True
    assert PlanContext(main_task="t", enable_intent_gate=False).enable_intent_gate is False


def test_disabled_gate_never_classifies_and_never_arms(fleet):
    ctx = _ctx(fleet, main_task="Analyse the provided measurements.",
               engineer_append_instructions="", enable_intent_gate=False)
    with _Classifier() as c:
        out = pf.preflight(_state(fleet, STEP_BASENAME), _rt(ctx))
    assert out.get("input_dir_empty") is True and c.calls == 0
    assert out.get("needs_provided_input") is None
    state = {**_state(fleet, STEP_BASENAME), **out, "needs_provided_input": True,
             "current_execution_verdict": ExecutionVerdict(status="success"),
             "data_manifest": [{"path": "data/x.csv", "bytes": 1}], "execution_stdout": "x"}
    assert sd_nodes._intent_gate_flagged(state, ctx) is False
    assert sd_nodes._intent_gate_fabrication(state, ctx) is False


# ── executor fallback (preflight off) reads the same fact ──────────────────


def test_executor_fallback_fact_matches_preflight(fleet, monkeypatch):
    """With `enable_preflight=False` the executor sets `input_dir_empty` on attempt 1; it
    must not tell the step_evaluator "everything loaded was self-created" when the task
    names the real input at an absolute path."""
    ctx = _ctx(fleet, enable_preflight=False)
    code = f"import pandas as pd\nprint(pd.read_csv({str(fleet.csv)!r}).shape)\n"
    state = {**_state(fleet, STEP_BASENAME), "current_code": _code(code)}
    out = sd_nodes.executor(state, _rt(ctx))
    assert out["execution_returncode"] == 0, out.get("execution_stderr")
    assert out.get("input_dir_empty") is False, out.get("input_dir_empty")
    # …and with nothing named anywhere it is still True
    ctx2 = _ctx(fleet, enable_preflight=False, main_task="t", engineer_append_instructions="")
    state2 = {**_state(fleet, STEP_NOUN), "current_code": _code("print(1)\n")}
    assert sd_nodes.executor(state2, _rt(ctx2)).get("input_dir_empty") is True


# ── safe_loading judges "absent" against what the task names ──────────────


def test_wrong_path_for_a_named_input_is_a_retry_not_missing(fleet):
    """qm9-joint Project35/Iteration1: the script raised FileNotFoundError('data/qm9.csv not
    found') while the task names the file at an absolute path. Wrong PATH, not missing INPUT."""
    ctx = _ctx(fleet)
    stderr = "Traceback (most recent call last):\nFileNotFoundError: data/qm9.csv not found"
    code = "import os\npath='data/qm9.csv'\nif not os.path.exists(path): raise FileNotFoundError(f'{path} not found')\n"
    state = {
        **_state(fleet, STEP_BASENAME), "input_dir_empty": False,
        "current_execution_verdict": ExecutionVerdict(status="failure", failure_kind="other",
                                                      error_summary="FileNotFoundError: data/qm9.csv not found"),
        "execution_stderr": stderr, "execution_stdout": "", "current_code": _code(code),
    }
    assert sd_nodes._missing_provided_input(state, ctx) is None
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "engineer"
    # parseable path with the same basename → same verdict
    state["execution_stderr"] = "FileNotFoundError: [Errno 2] No such file or directory: 'data/qm9.csv'"
    assert sd_nodes._missing_provided_input(state, ctx) is None
    # the REAL traceback of Project35/Iteration1: the code line inside the traceback makes
    # the path parser return the literal "{path} not found" — not a file name
    state["execution_stderr"] = (
        "Traceback (most recent call last):\n  File \"step_1.py\", line 7, in <module>\n"
        "    if not os.path.exists(path): raise FileNotFoundError(f'{path} not found')\n"
        "FileNotFoundError: data/qm9.csv not found"
    )
    assert sd_nodes._missing_provided_input(state, ctx) is None
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "engineer"
    # a DIFFERENT file that is genuinely absent is still missing
    state["execution_stderr"] = "FileNotFoundError: [Errno 2] No such file or directory: 'data/umap_embedding.npz'"
    assert sd_nodes._missing_provided_input(state, ctx) == "umap_embedding.npz"
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "missing_input"
    # and with nothing named anywhere the original verdict stands
    ctx2 = _ctx(fleet, main_task="Analyse the provided measurements.", engineer_append_instructions="")
    state["execution_stderr"] = stderr
    assert sd_nodes._missing_provided_input(state, ctx2) == "(required input file)"


def test_comma_delimited_csv_gets_no_decimal_comma_hint(fleet):
    report = "\n".join(pf._inspect_one(fleet.csv))
    assert "csv.Sniffer delimiter: ','" in report
    assert "comma appears inside numbers" not in report
    semi = fleet.data_root / "eu.csv"
    semi.write_text("a;b;c\n1,5;2,25;3\n4,0;5,5;6\n")
    report = "\n".join(pf._inspect_one(semi))
    assert "csv.Sniffer delimiter: ';'" in report and "comma appears inside numbers" in report
