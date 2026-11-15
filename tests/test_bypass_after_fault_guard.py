"""Gate test for the LIVE bypass-after-fault guard (`PlanContext.enable_bypass_after_fault_guard`).

Inside the engineer retry loop (`self_debug/nodes.py`), the guard remembers a real module-shaped
API fault and, on the next success that drops that module entirely rather than finding its
replacement, sends the step back to the engineer once with a steer note. That run exits 0 and
looks like a win, but the hand-rolled substitute can be silently wrong.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.prompts import render_retry_context
from cmbagent_lg.self_debug.schemas import EngineerResponse, ExecutionVerdict, StepVerdict


ENV_FLAG = "CMBAGENT_LG_BYPASS_AFTER_FAULT_GUARD"
REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"
os.environ.pop(REMOTE_ENV, None)

# A dotted ModuleNotFoundError, as raised for a real submodule import.
CAI_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 3, in <module>
    from Bio.SeqUtils.CodonUsage import CodonAdaptationIndex
ModuleNotFoundError: No module named 'Bio.SeqUtils.CodonUsage'
"""

ATTR_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 4, in <module>
    payload = json.load_all(fh)
AttributeError: module 'json' has no attribute 'load_all'
"""

IMPORT_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 2, in <module>
    from json import load_all
ImportError: cannot import name 'load_all' from 'json'
"""

# Out of scope for THIS guard — handled by the invented-kwarg / call-shape mechanism instead.
KWARG_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 9, in <module>
    sc.pp.normalize_total(adata, verbose=True)
TypeError: normalize_total() got an unexpected keyword argument 'verbose'
"""

ARTIST_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 12, in <module>
    sc.pl.pca(adata, dpi=300)
AttributeError: PathCollection.set() got an unexpected keyword argument 'dpi'
"""

NON_API_TB = """Traceback (most recent call last):
  File "/w/codebase/step_1.py", line 5, in <module>
    x = 1 / 0
ZeroDivisionError: division by zero
"""

# The bypassing code: drops Bio.SeqUtils.CodonUsage entirely and hand-rolls the computation.
BYPASS_CODE = """
import pandas as pd
from Bio import SeqIO
from Bio.Data import CodonTable

def main():
    pass

if __name__ == "__main__":
    main()
"""

# A code path that DID adapt — still references the faulted module (a fixed import).
RESOLVED_CODE = """
from Bio.SeqUtils.CodonUsage import CodonAdaptationIndex

def main():
    pass
"""


class _Env:
    """Set/restore env vars around a block (the flag is process-global)."""

    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


# ── classify: only the "module" shape ────────────────────────────────────


def test_classify_recognizes_the_module_shape():
    assert sd_nodes._bypass_guard_classify(CAI_TB) == ("Bio.SeqUtils", "CodonUsage")
    assert sd_nodes._bypass_guard_classify(ATTR_TB) == ("json", "load_all")
    assert sd_nodes._bypass_guard_classify(IMPORT_TB) == ("json", "load_all")
    print("ok test_classify_recognizes_the_module_shape")


def test_classify_rejects_the_kwarg_and_artist_shapes():
    """Those are the DIFFERENT invented-kwarg/call-shape mechanism — out of scope here."""
    assert sd_nodes._bypass_guard_classify(KWARG_TB) is None
    assert sd_nodes._bypass_guard_classify(ARTIST_TB) is None
    assert sd_nodes._bypass_guard_classify(NON_API_TB) is None
    assert sd_nodes._bypass_guard_classify(None) is None
    assert sd_nodes._bypass_guard_classify("") is None
    print("ok test_classify_rejects_the_kwarg_and_artist_shapes")


# ── still-references ──────────────────────────────────────────────────────


def test_still_references_the_faulted_module():
    assert sd_nodes._bypass_guard_still_references(
        RESOLVED_CODE, "Bio.SeqUtils", "CodonUsage") is True
    assert sd_nodes._bypass_guard_still_references(
        "import Bio.SeqUtils.CodonUsage\n", "Bio.SeqUtils", "CodonUsage") is True
    assert sd_nodes._bypass_guard_still_references(
        "from Bio.SeqUtils.CodonUsage import CodonAdaptationIndex as C\n",
        "Bio.SeqUtils", "CodonUsage") is True
    print("ok test_still_references_the_faulted_module")


def test_drops_the_faulted_module_entirely():
    assert sd_nodes._bypass_guard_still_references(
        BYPASS_CODE, "Bio.SeqUtils", "CodonUsage") is False
    print("ok test_drops_the_faulted_module_entirely")


def test_unparseable_code_is_conservative():
    assert sd_nodes._bypass_guard_still_references(
        "def broken(:\n", "Bio.SeqUtils", "CodonUsage") is True
    print("ok test_unparseable_code_is_conservative")


# ── _bypass_guard_update: the state-transition function ────────────────────


def _success(status="success"):
    return ExecutionVerdict(status=status)


def test_off_is_always_a_no_op():
    ctx = PlanContext(main_task="t")  # enable_bypass_after_fault_guard defaults False
    with _Env(**{ENV_FLAG: None}):
        assert sd_nodes._bypass_guard_update(
            {"execution_stderr": CAI_TB}, ctx, ExecutionVerdict(status="failure")) == {}
        assert sd_nodes._bypass_guard_update(
            {"bypass_guard_fault": ("Bio.SeqUtils", "CodonUsage"),
             "current_code": EngineerResponse(python_code=BYPASS_CODE, code_explanation="x")},
            ctx, _success()) == {}
    print("ok test_off_is_always_a_no_op")


def test_failure_records_the_fault():
    ctx = PlanContext(main_task="t", enable_bypass_after_fault_guard=True)
    with _Env(**{ENV_FLAG: None}):
        out = sd_nodes._bypass_guard_update(
            {"execution_stderr": CAI_TB}, ctx, ExecutionVerdict(status="failure"))
        assert out == {"bypass_guard_fault": ("Bio.SeqUtils", "CodonUsage")}
        # A non-API failure records nothing.
        out2 = sd_nodes._bypass_guard_update(
            {"execution_stderr": NON_API_TB}, ctx, ExecutionVerdict(status="failure"))
        assert out2 == {}
    print("ok test_failure_records_the_fault")


def test_never_engaged_success_is_a_hard_no_op():
    """A success with NO fault ever recorded — the guard was never engaged — must not touch
    state at all, flag on or not. This is what keeps a clean run free."""
    ctx = PlanContext(main_task="t", enable_bypass_after_fault_guard=True)
    with _Env(**{ENV_FLAG: None}):
        state = {
            "current_code": EngineerResponse(python_code=BYPASS_CODE, code_explanation="x"),
            # no "bypass_guard_fault" key at all — the fault never fired this step.
        }
        assert sd_nodes._bypass_guard_update(state, ctx, _success()) == {}
    print("ok test_never_engaged_success_is_a_hard_no_op")


def test_resolved_success_clears_the_fault_silently():
    ctx = PlanContext(main_task="t", enable_bypass_after_fault_guard=True)
    with _Env(**{ENV_FLAG: None}):
        state = {
            "bypass_guard_fault": ("Bio.SeqUtils", "CodonUsage"),
            "current_code": EngineerResponse(python_code=RESOLVED_CODE, code_explanation="x"),
        }
        out = sd_nodes._bypass_guard_update(state, ctx, _success())
        assert out == {"bypass_guard_fault": None}, "a genuine fix must not be nudged"
    print("ok test_resolved_success_clears_the_fault_silently")


def test_bypass_after_fault_fires_the_nudge():
    if __import__("importlib").util.find_spec("Bio") is None:
        print("SKIP test_bypass_after_fault_fires_the_nudge: needs biopython")
        return
    ctx = PlanContext(main_task="t", enable_bypass_after_fault_guard=True)
    with _Env(**{ENV_FLAG: None, REMOTE_ENV: None}):
        state = {
            "bypass_guard_fault": ("Bio.SeqUtils", "CodonUsage"),
            "current_code": EngineerResponse(python_code=BYPASS_CODE, code_explanation="x"),
        }
        out = sd_nodes._bypass_guard_update(state, ctx, _success())
    assert out["bypass_after_fault_detected_live"] is True
    assert out["bypass_guard_nudges"] == 1
    assert out["bypass_guard_fault"] is None
    assert "Bio.SeqUtils.CodonUsage" in out["bypass_after_fault_report"]
    assert "NUDGE" in out["bypass_after_fault_report"]
    # The nudge carries the replacement CLASS's own method inventory, not just its
    # constructor — a constructor-only nudge leaves the engineer guessing the method name
    # instead of finding the real one.
    assert "CodonAdaptationIndex(sequences" in out["bypass_after_fault_report"], (
        "constructor signature must still be present")
    assert "calculate(self, sequence)" in out["bypass_after_fault_report"], (
        "the class's own method inventory must be appended too")
    # also settable via the env var, exactly like the other flags in this feature family
    ctx_off = PlanContext(main_task="t")
    with _Env(**{ENV_FLAG: "1", REMOTE_ENV: None}):
        out2 = sd_nodes._bypass_guard_update(dict(state), ctx_off, _success())
    assert out2.get("bypass_after_fault_detected_live") is True
    print("ok test_bypass_after_fault_fires_the_nudge")


def test_nudge_is_bounded_to_once_per_step():
    ctx = PlanContext(main_task="t", enable_bypass_after_fault_guard=True)
    with _Env(**{ENV_FLAG: None}):
        state = {
            "bypass_guard_fault": ("Bio.SeqUtils", "CodonUsage"),
            "bypass_guard_nudges": 1,  # already nudged once
            "current_code": EngineerResponse(python_code=BYPASS_CODE, code_explanation="x"),
        }
        assert sd_nodes._bypass_guard_update(state, ctx, _success()) == {}
    print("ok test_nudge_is_bounded_to_once_per_step")


# ── router ───────────────────────────────────────────────────────────────


def test_router_sends_a_flagged_success_back_to_engineer_within_budget():
    ctx = PlanContext(main_task="t", max_n_attempts=4)
    state = {
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "bypass_after_fault_report": "**BYPASS-AFTER-FAULT NOTE...",
        "attempts": 2,
    }
    dest = sd_nodes.route_after_execution_evaluator(
        state, types.SimpleNamespace(context=ctx))
    assert dest == "engineer"
    print("ok test_router_sends_a_flagged_success_back_to_engineer_within_budget")


def test_router_lets_the_budget_win_when_exhausted():
    ctx = PlanContext(main_task="t", max_n_attempts=4, vlm_enabled=False)
    state = {
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "bypass_after_fault_report": "**BYPASS-AFTER-FAULT NOTE...",
        "attempts": 4,  # exhausted
    }
    dest = sd_nodes.route_after_execution_evaluator(
        state, types.SimpleNamespace(context=ctx))
    assert dest == "step_evaluator", "surface, don't force, once the budget is gone"
    print("ok test_router_lets_the_budget_win_when_exhausted")


def test_router_unaffected_when_no_report_is_set():
    """Regression guard: an ordinary success (the vast majority of runs, guard on or off)
    must route exactly as before."""
    ctx = PlanContext(main_task="t", max_n_attempts=4, vlm_enabled=False)
    state = {"current_execution_verdict": ExecutionVerdict(status="success"), "attempts": 1}
    dest = sd_nodes.route_after_execution_evaluator(
        state, types.SimpleNamespace(context=ctx))
    assert dest == "step_evaluator"
    print("ok test_router_unaffected_when_no_report_is_set")


# ── the real engineer node ──────────────────────────────────────────────


def _run_engineer(ctx, bypass_report):
    captured = {}

    class _FakeLLM:
        def invoke(self, msgs, config=None):
            captured["system"] = msgs[0].content
            return types.SimpleNamespace(text="```python\nprint(1)\n```")

    state = {
        "step": Step(sub_task="compute CAI", sub_task_agent="engineer",
                     bullet_points=["use biopython"]),
        "attempts": 1,  # bumped to 2 -> retry mode
        "current_code": EngineerResponse(python_code=BYPASS_CODE, code_explanation="x"),
        "current_execution_verdict": ExecutionVerdict(status="success"),
        "execution_stdout": "gene1: CAI=0.9016\n",
        "execution_stderr": "",
        "execution_returncode": 0,
        "execution_timed_out": False,
        "error_history": [],
        "bypass_after_fault_report": bypass_report,
        # Stale leftovers from an EARLIER attempt: they belong to an attempt the router
        # short-circuited past, so they must not leak into this retry's prompt.
        "current_step_verdict": StepVerdict(
            fulfilled=False, unmet_requirements=["stale, unrelated requirement"],
            feedback="stale feedback from a different attempt"),
    }
    saved = sd_nodes._proposer
    sd_nodes._proposer = lambda _c: _FakeLLM()
    try:
        sd_nodes.engineer(state, types.SimpleNamespace(context=ctx))
    finally:
        sd_nodes._proposer = saved
    return captured["system"]


def test_engineer_node_injects_the_nudge_and_clears_it():
    report = sd_nodes._bypass_guard_report("Bio.SeqUtils", "CodonUsage")
    on = _run_engineer(PlanContext(main_task="t", enable_bypass_after_fault_guard=True), report)
    off = _run_engineer(PlanContext(main_task="t"), None)
    assert "BYPASS-AFTER-FAULT NOTE" in on
    assert "Bio.SeqUtils.CodonUsage" in on
    assert "BYPASS-AFTER-FAULT NOTE" not in off
    # stale step verdict must NOT be shown alongside the bypass note
    assert "stale, unrelated requirement" not in on
    assert "stale feedback from a different attempt" not in on
    # the code that actually ran (bypassing) IS shown, so the engineer can see what to fix
    assert "def main" in on
    print("ok test_engineer_node_injects_the_nudge_and_clears_it")


def test_render_retry_context_bypass_mode_shows_stdout_and_note():
    block = render_retry_context(
        attempts=2, max_attempts=4, last_code=BYPASS_CODE, last_stderr=None,
        last_stdout="gene1: CAI=0.9016\n", last_returncode=0, last_timed_out=False,
        last_fix_suggestion=None, error_history=[],
        bypass_after_fault_report="**BYPASS-AFTER-FAULT NOTE (deterministic...) test-marker**",
    )
    assert "gene1: CAI=0.9016" in block
    assert "BYPASS-AFTER-FAULT NOTE" in block
    assert "test-marker" in block
    print("ok test_render_retry_context_bypass_mode_shows_stdout_and_note")


def test_render_retry_context_off_is_byte_identical():
    kwargs = dict(
        attempts=2, max_attempts=4, last_code=BYPASS_CODE, last_stderr=None,
        last_stdout="gene1: CAI=0.9016\n", last_returncode=0, last_timed_out=False,
        last_fix_suggestion=None, error_history=[],
    )
    off = render_retry_context(**kwargs)
    off_explicit_none = render_retry_context(bypass_after_fault_report=None, **kwargs)
    assert off == off_explicit_none
    assert "BYPASS-AFTER-FAULT" not in off
    print("ok test_render_retry_context_off_is_byte_identical")


if __name__ == "__main__":
    test_classify_recognizes_the_module_shape()
    test_classify_rejects_the_kwarg_and_artist_shapes()
    test_still_references_the_faulted_module()
    test_drops_the_faulted_module_entirely()
    test_unparseable_code_is_conservative()
    test_off_is_always_a_no_op()
    test_failure_records_the_fault()
    test_never_engaged_success_is_a_hard_no_op()
    test_resolved_success_clears_the_fault_silently()
    test_bypass_after_fault_fires_the_nudge()
    test_nudge_is_bounded_to_once_per_step()
    test_router_sends_a_flagged_success_back_to_engineer_within_budget()
    test_router_lets_the_budget_win_when_exhausted()
    test_router_unaffected_when_no_report_is_set()
    test_engineer_node_injects_the_nudge_and_clears_it()
    test_render_retry_context_bypass_mode_shows_stdout_and_note()
    test_render_retry_context_off_is_byte_identical()
    print("\nALL bypass-after-fault guard tests passed.")
