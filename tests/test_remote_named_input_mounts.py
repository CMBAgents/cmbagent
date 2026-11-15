"""Gate test: a provided input on a GUEST-ONLY mount is not "missing".

`resolve_named_input` decides "is this a provided input?" by asking whether the named
path exists — and it asked the engine's filesystem. Under a remote executor the inputs
live on the guest: the remote executor mounts uploaded datasets read-only at `/datasets/<id>/…`
(gcsfuse on the VM, bind-mounted into the sandbox) while the engine is a separate
container that has no such path. So the stat said "absent", `input_dir_empty` stayed
True, the gate armed on a step whose input was right there, and the clean load was then
failed as fabricated.

Measured on run-0002-example (deep_research, cpu, iris.csv mounted at
/datasets/ds-0001/Iris.csv): the step read 150 real rows and exited 0, the
execution_evaluator said "success", and the next node was the terminal `missing_input`
with the reason quoting the very path it had been told to load. Plan halted 1/2.

The listing fix (`_remote_data_empty`) does not reach this: `list_files` answers for the
session's `data/` only, and a read-only dataset mount is a different mount. Pre-flight now
ships the candidate paths to the guest via `introspect` — the same sandbox `execute` uses,
so the same mounts — and fail-opens to UNKNOWN when the guest cannot answer.

Run:  python tests/test_remote_named_input_mounts.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug import preflight as pf
from cmbagent_lg.self_debug.remote_exec import RemoteExecutor
from cmbagent_lg.self_debug.schemas import ExecutionVerdict, IntentVerdict

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
MOUNTED = "/datasets/ds-0001/Iris.csv"

_TMP = tempfile.mkdtemp(prefix="named-input-mount-")
WORK_DIR = Path(_TMP) / "runs" / "run-0002-example"
(WORK_DIR / "data").mkdir(parents=True, exist_ok=True)

# Step 1 of the cited run, as the planner wrote it.
LOADER = Step(
    sub_task="Load Iris dataset, compute row count, column names, and grouped means, "
             "then save results to disk.",
    sub_task_agent="engineer",
    bullet_points=[f"Load the Iris dataset from `{MOUNTED}` using pandas.",
                   "Calculate the total row count and extract the list of column names.",
                   "Group numeric features by the species column and calculate the mean."],
)
# A step that is supposed to CREATE its data and names no path at all.
GENERATOR = Step(
    sub_task="Simulate 12000 synthetic datasets and save them for the next step.",
    sub_task_agent="engineer",
    bullet_points=["Draw the samples with numpy.", "Save them under the data directory."],
)
# Same, but it names its OUTPUT. The path is probed and correctly not found: an output
# does not exist yet at pre-flight time, which is what tells it apart from an input.
GENERATOR_NAMING_ITS_OUTPUT = Step(
    sub_task="Simulate 12000 synthetic datasets and save them for the next step.",
    sub_task_agent="engineer",
    bullet_points=["Draw the samples with numpy.", "Save them to data/synthetic.h5."],
)


class _Env:
    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class _Guest:
    """Stands in for `RemoteExecutor._request`. `found` is what the sandbox's isfile()
    probe prints; `raises`/`reply` drive the fail-open cases."""

    def __init__(self, found=None, raises=None, reply=None, data_files=None):
        self.found, self.raises, self.reply = found, raises, reply
        self.data_files = data_files or []
        self.seen = []

    def __enter__(self):
        self.saved = RemoteExecutor._request
        guest = self

        def _request(self_, msg, timeout=60):
            guest.seen.append(msg)
            if msg.get("type") == "list_files":       # the session data/, still empty
                return {"type": "listing", "files": guest.data_files}
            if guest.raises is not None:
                raise guest.raises
            if guest.reply is not None:
                return guest.reply
            if msg.get("type") != "introspect":
                return {"type": "error", "message": "unexpected"}
            return {"type": "introspect_result", "returncode": 0,
                    "stdout": (guest.found + "\n") if guest.found else "", "stderr": ""}

        RemoteExecutor._request = _request
        return self

    def __exit__(self, *exc):
        RemoteExecutor._request = self.saved

    @property
    def probes(self):
        return [m for m in self.seen if m.get("type") == "introspect"]


class _Classifier:
    def __init__(self, needs_provided_input=True):
        self.verdict = IntentVerdict(
            needs_provided_input=needs_provided_input,
            reason=f"The step explicitly instructs to 'Load the Iris dataset from `{MOUNTED}`'.",
        )
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


def _ctx(main_task="Analyse the Iris dataset."):
    return PlanContext(main_task=main_task, enable_preflight=True, enable_intent_gate=True)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(step=LOADER):
    return {"step": step, "work_dir": str(WORK_DIR), "step_number": 1, "attempts": 0}


def _success_state(out):
    """What the run really produced: exit 0, real columns, a written artifact."""
    return {
        **_state(), **out,
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "data_manifest": [{"path": "data/iris_grouped_means.csv", "bytes": 240}],
        "execution_stdout": "Total Row Count: 150\nColumn Names: ['Id', 'SepalLengthCm', "
                            "'SepalWidthCm', 'PetalLengthCm', 'PetalWidthCm', 'Species']\n",
    }


# ── the premise ─────────────────────────────────────────────────────────────


def test_the_mounted_path_does_not_exist_on_the_engine():
    """If this ever fails the rest says nothing: the whole defect is that the engine
    cannot see the mount, so its own resolver must come back empty."""
    assert not Path(MOUNTED).exists()
    assert pf.resolve_named_input(f"Load `{MOUNTED}`", WORK_DIR) is None
    print("ok test_the_mounted_path_does_not_exist_on_the_engine")


def test_the_candidate_list_carries_the_mounted_path():
    cands = pf._candidate_paths(f"Load the Iris dataset from `{MOUNTED}` using pandas.", WORK_DIR)
    assert MOUNTED in cands, cands
    print("ok test_the_candidate_list_carries_the_mounted_path")


def test_a_relative_token_stays_relative_for_the_guest():
    """The guest evaluates from the session dir (the script's cwd). Binding a relative
    token to the ENGINE's work_dir would name a path only this process has."""
    cands = pf._candidate_paths("Load data/qm9.csv and summarise it.", WORK_DIR)
    assert "data/qm9.csv" in cands, cands
    assert not any(c.startswith(str(WORK_DIR)) for c in cands), cands
    print("ok test_a_relative_token_stays_relative_for_the_guest")


# ── the fix ─────────────────────────────────────────────────────────────────


def test_the_guest_resolves_the_mounted_dataset():
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=MOUNTED) as g, _Classifier() as c:
        out = pf.preflight(_state(), _rt(ctx))
    assert out.get("input_dir_empty") is False, out
    assert "needs_provided_input" not in out, out
    assert c.calls == 0, "the gate must not even ask once the input is reachable"
    assert len(g.probes) == 1, g.seen
    assert MOUNTED in out.get("preflight_report", ""), out
    print("ok test_the_guest_resolves_the_mounted_dataset")


def test_the_iris_halt_no_longer_routes_to_missing_input():
    """The live regression: run-0002-example step 1, exit 0 on 150 real rows."""
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=MOUNTED), _Classifier():
        out = pf.preflight(_state(), _rt(ctx))
    state = _success_state(out)
    assert sd_nodes._intent_gate_fabrication(state, ctx) is False, state
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) != "missing_input"
    print("ok test_the_iris_halt_no_longer_routes_to_missing_input")


def test_the_path_named_only_in_main_task_is_probed():
    """Denario puts the dataset path in `main_task`, not in the planner's bullet."""
    bare = Step(sub_task="Load the Iris dataset and report its shape.",
                sub_task_agent="engineer", bullet_points=["Load `Iris.csv` with pandas."])
    ctx = _ctx(main_task=f"The Iris dataset is mounted at `{MOUNTED}`.")
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=MOUNTED) as g, _Classifier():
        out = pf.preflight(_state(bare), _rt(ctx))
    assert out.get("input_dir_empty") is False, out
    assert MOUNTED in g.probes[0]["argv"][0], g.probes[0]
    print("ok test_the_path_named_only_in_main_task_is_probed")


# ── the guard still has teeth ───────────────────────────────────────────────


def test_a_named_but_absent_path_still_arms_the_gate():
    """Genuine fabrication: the task names an input, and the guest says it is not there."""
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=None), _Classifier() as c:
        out = pf.preflight(_state(), _rt(ctx))
    assert out.get("input_dir_empty") is True, out
    assert out.get("needs_provided_input") is True, out
    assert c.calls == 1
    state = _success_state(out)
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) == "missing_input"
    print("ok test_a_named_but_absent_path_still_arms_the_gate")


def test_a_generate_step_names_nothing_and_is_never_probed():
    ctx = _ctx(main_task="Simulate and analyse.")
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=None) as g, _Classifier(False):
        out = pf.preflight(_state(GENERATOR), _rt(ctx))
    assert out.get("input_dir_empty") is True, out
    assert g.probes == [], g.seen
    print("ok test_a_generate_step_names_nothing_and_is_never_probed")


def test_a_named_output_is_probed_and_still_leaves_the_dir_empty():
    """An OUTPUT is named but does not exist yet, so the probe finds nothing and the fact
    stays True — existence is what separates a provided input from a file to be written."""
    ctx = _ctx(main_task="Simulate and analyse.")
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=None) as g, _Classifier(False):
        out = pf.preflight(_state(GENERATOR_NAMING_ITS_OUTPUT), _rt(ctx))
    assert out.get("input_dir_empty") is True, out
    assert len(g.probes) == 1, g.seen
    sent = g.probes[0]["argv"][0]
    assert "data/synthetic.h5" in sent, sent
    # and no bogus directory was synthesised out of the relative token's tail
    assert "/synthetic.h5." not in sent, sent
    print("ok test_a_named_output_is_probed_and_still_leaves_the_dir_empty")


# ── a probe that cannot run changes nothing ─────────────────────────────────
#
# The data/ listing fail-opens to UNKNOWN because its failure leaves the emptiness itself
# undetermined. Here it is already determined, so an unanswered probe may only cost us the
# fix — never the guard. A guard that switches itself off on a transient error is not one.


def _unchanged(out, c):
    assert out.get("input_dir_empty") is True, out
    assert out.get("needs_provided_input") is True, out
    assert c.calls == 1


def test_an_unreachable_guest_changes_nothing():
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(raises=OSError("connection refused")), \
            _Classifier() as c:
        out = pf.preflight(_state(), _rt(ctx))
    _unchanged(out, c)
    print("ok test_an_unreachable_guest_changes_nothing")


def test_a_guest_too_old_to_introspect_changes_nothing():
    """R-17 added the verb; a guest baked before it names what it does serve."""
    ctx = _ctx()
    reply = {"type": "error", "message": "unknown request type 'introspect'; this guest serves: "
                                         "ping, execute, list_files"}
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(reply=reply), _Classifier() as c:
        out = pf.preflight(_state(), _rt(ctx))
    _unchanged(out, c)
    print("ok test_a_guest_too_old_to_introspect_changes_nothing")


def test_a_timed_out_probe_changes_nothing():
    ctx = _ctx()
    reply = {"type": "introspect_result", "returncode": 124, "stdout": "",
             "stderr": "introspect timed out"}
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(reply=reply), _Classifier() as c:
        out = pf.preflight(_state(), _rt(ctx))
    _unchanged(out, c)
    # …and the terminal verdict is the one it would have had before this fix existed.
    assert sd_nodes.route_after_execution_evaluator(
        _success_state(out), _rt(ctx)) == "missing_input"
    print("ok test_a_timed_out_probe_changes_nothing")


# ── nothing else changes ────────────────────────────────────────────────────


def test_a_populated_session_data_dir_is_never_probed():
    """`list_files` already answered; there is no question left to ask."""
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), \
            _Guest(data_files=[{"name": "prior.csv", "bytes": 10}]) as g, _Classifier():
        out = pf.preflight(_state(), _rt(ctx))
    assert out.get("input_dir_empty") is False, out
    assert g.probes == [], g.seen
    print("ok test_a_populated_session_data_dir_is_never_probed")


def test_the_local_path_sends_nothing():
    """No remote executor → no probe, and the engine's own disk decides, as before."""
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: None}), _Guest(found=MOUNTED) as g, _Classifier():
        pf.preflight(_state(), _rt(ctx))
    assert g.seen == [], g.seen
    print("ok test_the_local_path_sends_nothing")


def test_a_local_file_still_resolves_without_a_probe():
    """The f480012 case: a real input on the engine's own disk needs no guest."""
    local = WORK_DIR.parent / "provided_input.csv"
    local.write_text("a,b\n1,2\n")
    step = Step(sub_task="Load the provided data.", sub_task_agent="engineer",
                bullet_points=[f"Read `{local}` with pandas."])
    ctx = _ctx(main_task="Analyse the provided file.")
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(found=None) as g, _Classifier():
        out = pf.preflight(_state(step), _rt(ctx))
    assert out.get("input_dir_empty") is False, out
    assert g.probes == [], g.seen
    print("ok test_a_local_file_still_resolves_without_a_probe")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL REMOTE NAMED-INPUT MOUNT TESTS PASSED")
