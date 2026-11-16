"""Gate test: under `CMBAGENT_LG_REMOTE_EXECUTOR`, `input_dir_empty` describes the GUEST
session's data/, not the engine's.

The engine's `work_dir/data` is never the execution dir in remote mode (`nodes._run_remote`
ships the step to the guest and the data lives guest-side), so it is empty on EVERY step.
Pre-flight read that dir anyway: a step 2+ that consumes step 1's artifacts ("load … from
data/") armed the intent gate, and its clean success was then read as fabricated data →
terminal `missing_input`, step not fulfilled, plan ended. Measured on run-0001-example
(full_paper, remote CPU, no uploads): step 2 returned rc=0 having written em_estimates.csv
from the 12000 datasets step 1 left in the session's data/.

The executor already refuses to set the fact in remote mode; pre-flight now asks the guest
(`RemoteExecutor.list_files`) instead of asserting the engine's own dir.
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

_TMP = tempfile.mkdtemp(prefix="intent-remote-")
WORK_DIR = Path(_TMP) / "runs" / "run-0001-example"
(WORK_DIR / "data").mkdir(parents=True, exist_ok=True)

# Step 2 of the cited run: it consumes what step 1 wrote, so the wording is a load-from-data/
# consumer and the classifier answers True.
CONSUMER = Step(
    sub_task="Vectorized Batch Maximum Likelihood Estimation via Expectation-Maximization",
    sub_task_agent="engineer",
    bullet_points=["Load synthetic datasets and theoretical bounds from data/.",
                   "Save the estimates to data/em_estimates.csv."],
)
# What step 1 left in the session's data/ (guest-side, invisible to the engine).
PRIOR_STEP_FILES = [
    {"name": "synthetic_datasets.h5", "bytes": 124_000_000},
    {"name": "theoretical_bounds.csv", "bytes": 4096},
]


class _Env:
    """Set/restore env vars around a block (the remote spec is process-global)."""

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
    """Stands in for `RemoteExecutor._request` — the engine-side transport under test."""

    def __init__(self, files=None, raises=None, reply=None):
        self.files, self.raises, self.reply, self.seen = files or [], raises, reply, []

    def __enter__(self):
        self.saved = RemoteExecutor._request
        guest = self

        def _request(self_, msg, timeout=60):
            guest.seen.append(msg)
            if guest.raises is not None:
                raise guest.raises
            if guest.reply is not None:
                return guest.reply
            if msg.get("type") != "list_files":
                return {"type": "error", "message": "unexpected"}
            return {"type": "listing", "files": guest.files}

        RemoteExecutor._request = _request
        return self

    def __exit__(self, *exc):
        RemoteExecutor._request = self.saved


class _Classifier:
    """The gate's one model call, stubbed: the bug under test is the emptiness FACT, not the
    LLM. Counts calls, so 'never asked' is provable."""

    def __init__(self, needs_provided_input=True):
        self.verdict = IntentVerdict(
            needs_provided_input=needs_provided_input,
            reason="The step explicitly instructs to 'Load synthetic datasets and "
                   "theoretical bounds from data/.'",
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


def _ctx():
    return PlanContext(main_task="t", enable_preflight=True, enable_intent_gate=True)


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _state(step=CONSUMER):
    return {"step": step, "work_dir": str(WORK_DIR), "step_number": 2, "attempts": 0}


def _clear_engine_data():
    for p in (WORK_DIR / "data").iterdir():
        p.unlink()


# ── the fact ────────────────────────────────────────────────────────────────


def test_the_engine_data_dir_is_empty_in_remote_mode():
    """The premise: nothing engine-side ever populates work_dir/data under a remote executor,
    so every step sees it empty. If this stops being true the tests below say nothing."""
    _clear_engine_data()
    assert not any(p.is_file() for p in (WORK_DIR / "data").iterdir())
    print("ok test_the_engine_data_dir_is_empty_in_remote_mode")


def test_prior_step_output_on_the_guest_is_not_missing_input():
    """THE BUG. Engine data/ empty, guest session data/ holds step 1's artifacts: the step's
    input EXISTS, so the gate must not arm and the classifier must never be asked."""
    _clear_engine_data()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(PRIOR_STEP_FILES) as g, _Classifier() as c:
        out = pf.preflight(_state(), _rt(_ctx()))
    assert out.get("input_dir_empty") is False, (
        f"the engine's own data/ decided a fact about the guest's: {out}")
    assert out.get("needs_provided_input") is None, out
    assert c.calls == 0, "the classifier was asked although the input exists"
    assert g.seen and g.seen[0]["type"] == "list_files", g.seen
    assert g.seen[0]["session"] == "run-0001-example", (
        f"the listing used the wrong session — `_run_remote` uses work_dir.name: {g.seen[0]}")
    print("ok test_prior_step_output_on_the_guest_is_not_missing_input")


def test_a_genuinely_empty_guest_still_arms_the_gate():
    """The inverse: nothing on the guest either, so the missing-input case survives intact."""
    _clear_engine_data()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest([]) as g, _Classifier() as c:
        out = pf.preflight(_state(), _rt(_ctx()))
    assert out.get("input_dir_empty") is True, out
    assert out.get("needs_provided_input") is True, out
    assert c.calls == 1 and g.seen, (c.calls, g.seen)
    assert sd_nodes._intent_gate_flagged({**_state(), **out}, _ctx()) is True
    print("ok test_a_genuinely_empty_guest_still_arms_the_gate")


def test_a_guest_that_cannot_answer_leaves_the_fact_UNKNOWN():
    """Fail-open, as everywhere else on this seam (`api_introspect.env_report`,
    `_classify_intent`): a listing the guest cannot serve is NOT evidence of emptiness, so the
    fact stays unset and neither the gate nor the step_evaluator's safe_loading rule fires.
    A crash here would take the whole graph down."""
    _clear_engine_data()
    for guest in (_Guest(raises=ConnectionRefusedError("guest down")),
                  _Guest(reply={"type": "error", "message": "no such session"}),
                  _Guest(reply={"type": "listing"})):
        with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), guest, _Classifier() as c:
            out = pf.preflight(_state(), _rt(_ctx()))
        assert "input_dir_empty" not in out, out
        assert out.get("needs_provided_input") is None, out
        assert c.calls == 0, "the gate was armed on an unanswered listing"
        assert sd_nodes._intent_gate_flagged({**_state(), **out}, _ctx()) is False
    print("ok test_a_guest_that_cannot_answer_leaves_the_fact_UNKNOWN")


def test_a_populated_engine_dir_asks_the_guest_nothing():
    """Cost guard: one listing RPC on the empty-engine path only. Files here mean the engine IS
    the exec dir."""
    _clear_engine_data()
    (WORK_DIR / "data" / "provided.csv").write_text("a,b\n1,2\n")
    try:
        with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(PRIOR_STEP_FILES) as g, _Classifier() as c:
            out = pf.preflight(_state(), _rt(_ctx()))
    finally:
        _clear_engine_data()
    assert out.get("input_dir_empty") is False, out
    assert g.seen == [], "a listing was sent although data/ already had files"
    assert c.calls == 0, out
    print("ok test_a_populated_engine_dir_asks_the_guest_nothing")


# ── the local path is untouched ─────────────────────────────────────────────


def test_local_execution_still_judges_the_engine_dir():
    _clear_engine_data()
    with _Env(**{REMOTE_ENV: None}), _Guest(PRIOR_STEP_FILES) as g, _Classifier() as c:
        out = pf.preflight(_state(), _rt(_ctx()))
    assert out.get("input_dir_empty") is True, out
    assert out.get("needs_provided_input") is True and c.calls == 1, out
    assert g.seen == [], "the local path sent a remote request"
    print("ok test_local_execution_still_judges_the_engine_dir")


# ── the consequence the fact drives ─────────────────────────────────────────


def _success_state(preflight_out):
    return {
        **_state(), **preflight_out,
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "data_manifest": [{"path": "data/em_estimates.csv", "bytes": 512}],
        "execution_stdout": "Total Datasets Processed 12000\n",
    }


def test_the_clean_remote_step_is_no_longer_terminated():
    """End of the chain: with the fact right, a step that really loaded its input routes to the
    evaluators instead of the terminal `missing_input` node that ended the plan."""
    _clear_engine_data()
    ctx = _ctx()
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest(PRIOR_STEP_FILES), _Classifier():
        out = pf.preflight(_state(), _rt(ctx))
    state = _success_state(out)
    assert sd_nodes._intent_gate_fabrication(state, ctx) is False, state
    assert sd_nodes.route_after_execution_evaluator(state, _rt(ctx)) != "missing_input"
    # …and the real thing still halts.
    with _Env(**{REMOTE_ENV: "10.0.0.5:8790"}), _Guest([]), _Classifier():
        out_empty = pf.preflight(_state(), _rt(ctx))
    assert sd_nodes.route_after_execution_evaluator(
        _success_state(out_empty), _rt(ctx)) == "missing_input"
    print("ok test_the_clean_remote_step_is_no_longer_terminated")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nALL REMOTE INTENT-GATE DATA TESTS PASSED")
