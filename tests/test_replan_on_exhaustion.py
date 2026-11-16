"""Replan on exhaustion: a step that burns its whole attempt budget rewrites the plan
instead of ending the run.

Three independent places turned an exhausted step into a halt: `self_debug`'s goal gate
ENDs on exhaustion, `adaptive_review` early-returned on ANY failed step, and
`route_after_review` returned END. So the replanner — the one component that could have
rewritten the plan — was switched off in exactly the case where the plan was the
problem. Eight breast-cancer-fleet projects died there, spending 30% of all attempt
wall-clock arguing with the step evaluator before halting.

`_step_exhausted` marks the outcome (`exhausted`) and `_replannable_exhaustion` decides,
before any LLM call, whether the replanner may be asked. HALT STAYS THE FALLBACK:
adaptive planning off, a spent `max_adaptations`, a structurally unfulfillable step, a
transient failure, or a reviewer with nothing to change all end the run exactly as
before. The existing caps are untouched — `tests/test_adaptive_caps.py` and
`tests/adv_rt3_halt_on_failure.py` still hold.

Run:
    python tests/test_replan_on_exhaustion.py
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.deep_research import nodes as dr_nodes
from cmbagent_lg.deep_research import schemas as dr_schemas
from cmbagent_lg.deep_research.graph import build_deep_research_graph
from cmbagent_lg.deep_research.prompts import (
    adaptive_replanner_user,
    adaptive_reviewer_user,
)
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.self_debug.schemas import EngineerResponse, StepVerdict

UNMET = "fit a Fine-Gray competing-risks regression"


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(name, agent="engineer"):
    return Step(sub_task=name, sub_task_agent=agent, bullet_points=[f"do {name}"])


# ── (1) `_step_exhausted`: what counts as "spent its budget" ─────────────────


def _sub(attempts=3, **kw):
    st = {"attempts": attempts, "current_step_verdict": StepVerdict(
        fulfilled=False, unmet_requirements=[UNMET], feedback="again")}
    st.update(kw)
    return st


def test_a_data_infeasible_stop_is_replannable_though_the_budget_remains():
    """It stops EARLY, so its attempts are below the cap; without this the early stop
    would halt the run sooner than the eight-attempt grind it replaces."""
    ctx = PlanContext(main_task="t", max_n_attempts=8)
    eng = _step("s")
    sub = _sub(2, execution_infeasible_signal="non_convergence: delta contains nan")
    assert dr_nodes._step_exhausted(sub, eng, ctx) is True
    # Still excluded when the input does not exist: a rewrite could only invent one.
    sub["unfulfillable"] = True
    assert dr_nodes._step_exhausted(sub, eng, ctx) is False


def test_step_exhausted_predicate():
    ctx = PlanContext(main_task="t", max_n_attempts=3)
    eng = _step("s")
    assert dr_nodes._step_exhausted(_sub(3), eng, ctx) is True
    assert dr_nodes._step_exhausted(_sub(4), eng, ctx) is True
    # A transient failure with budget left is not exhaustion — the sub-graph will
    # retry, and this failure is not the replanner's business.
    assert dr_nodes._step_exhausted(_sub(1), eng, ctx) is False
    # Structurally unfulfillable: the required input does not exist, so a rewritten
    # plan could only pressure the engineer into inventing a stand-in.
    assert dr_nodes._step_exhausted(_sub(3, unfulfillable=True), eng, ctx) is False
    assert dr_nodes._step_exhausted(
        _sub(3, data_fixer_format="missing"), eng, ctx) is False
    # The researcher's claim audit is not a planning problem.
    assert dr_nodes._step_exhausted(
        _sub(3, claim_audit_failed=True), eng, ctx) is False
    # The channel is the engineer path's.
    assert dr_nodes._step_exhausted(_sub(3), _step("s", "researcher"), ctx) is False
    # No context (a unit-tested router) falls back to the default budget.
    assert dr_nodes._step_exhausted(_sub(3), eng, None) is True


def test_run_step_stamps_the_outcome(monkeypatch):
    """`run_step` is what records `exhausted` plus the diagnostics the replanner
    needs: what the evaluator kept rejecting, and which names are PROVEN absent."""
    sub = _sub(3, step_number=1,
               current_code=EngineerResponse(python_code="print(1)", code_explanation="x"),
               execution_stdout="",
               api_preflight_bounced={"lifelines.CRCSplineFitter": 1})
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke", lambda *a, **k: sub,
                        raising=False)
    out = dr_nodes.run_step(
        {"plan": Plan(sub_tasks=[_step("s1")]), "step_index": 1, "work_dir": None},
        _rt(PlanContext(main_task="t", max_n_attempts=3)))
    outcome = out["step_outcomes"][-1]
    assert outcome["fulfilled"] is False
    assert outcome["exhausted"] is True
    assert outcome["unmet_requirements"] == [UNMET]
    assert outcome["api_preflight_findings"] == ["lifelines.CRCSplineFitter"]

    # The unfulfillable counterpart carries no `exhausted`, so it halts as before.
    sub["unfulfillable"] = True
    out = dr_nodes.run_step(
        {"plan": Plan(sub_tasks=[_step("s1")]), "step_index": 1, "work_dir": None},
        _rt(PlanContext(main_task="t", max_n_attempts=3)))
    assert "exhausted" not in out["step_outcomes"][-1]


# ── (2) `_replannable_exhaustion` + the router ───────────────────────────────


def _state(outcomes, adaptations=0, plan_len=3, step_index=None):
    plan = Plan(sub_tasks=[_step(f"s{i}") for i in range(1, plan_len + 1)])
    return {
        "plan": plan,
        "step_index": step_index if step_index is not None else len(outcomes) + 1,
        "step_outcomes": outcomes,
        "adaptive_history": [{"after_step": i} for i in range(adaptations)],
    }


_EXHAUSTED = [{"step_number": 1, "fulfilled": False, "exhausted": True,
               "attempts": 3, "reason": "still unmet"}]
_TRANSIENT = [{"step_number": 1, "fulfilled": False, "attempts": 1, "reason": "boom"}]
_OK = [{"step_number": 1, "fulfilled": True, "attempts": 1}]


def test_replannable_only_for_an_exhausted_step_with_budget():
    on = _rt(PlanContext(main_task="t", max_adaptations=3))
    off = _rt(PlanContext(main_task="t", adaptive_planning=False, max_adaptations=3))
    assert dr_nodes._replannable_exhaustion(_state(_EXHAUSTED), on) is True
    assert dr_nodes._replannable_exhaustion(_state(_EXHAUSTED), off) is False
    assert dr_nodes._replannable_exhaustion(_state(_TRANSIENT), on) is False
    assert dr_nodes._replannable_exhaustion(_state(_OK), on) is False
    assert dr_nodes._replannable_exhaustion(_state([]), on) is False
    # The adaptation cap is checked BEFORE the reviewer's LLM call.
    assert dr_nodes._replannable_exhaustion(_state(_EXHAUSTED, adaptations=3), on) is False
    assert dr_nodes._replannable_exhaustion(
        _state(_EXHAUSTED), _rt(PlanContext(main_task="t", max_adaptations=0))) is False


def test_router_sends_an_exhausted_step_to_the_replanner():
    ctx = _rt(PlanContext(main_task="t", max_adaptations=3))
    adapt = dr_schemas.AdaptiveReview(needs_adaptation=True, recommendations=["try X"])
    st = _state(_EXHAUSTED)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == "replan_tail"
    # Even with no tail left: a rewritten tail may be what finishes the task.
    st = _state(_EXHAUSTED, plan_len=1, step_index=2)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == "replan_tail"
    # Halt is the fallback — the reviewer had nothing to change.
    st = _state(_EXHAUSTED)
    st["adaptive_review"] = dr_schemas.AdaptiveReview(
        needs_adaptation=False, recommendations=[])
    assert dr_nodes.route_after_review(st, ctx) == END
    st = _state(_EXHAUSTED)
    st["adaptive_review"] = dr_schemas.AdaptiveReview(
        needs_adaptation=True, recommendations=[])       # nothing actionable
    assert dr_nodes.route_after_review(st, ctx) == END
    # …and so are a spent budget, adaptive planning off, and any other failure.
    st = _state(_EXHAUSTED, adaptations=3)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == END
    st = _state(_EXHAUSTED)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(
        st, _rt(PlanContext(main_task="t", adaptive_planning=False))) == END
    st = _state(_TRANSIENT)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == END
    # Unchanged on the happy paths.
    st = _state(_OK)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == "replan_tail"
    st = _state(_OK, plan_len=1, step_index=2)
    st["adaptive_review"] = adapt
    assert dr_nodes.route_after_review(st, ctx) == END


def test_adaptive_review_skips_the_llm_unless_it_can_act():
    """The early-return still covers every non-replannable case, so those runs make no
    reviewer call at all. `chat_model` is booby-trapped to prove it."""
    def _boom(*a, **k):
        raise AssertionError("the reviewer LLM was called on a halt path")

    def _no_verdict(out):
        # @timed_node always appends `node_elapsed_s`; an early return adds nothing else.
        assert "adaptive_review" not in out, out

    saved = dr_nodes.chat_model
    dr_nodes.chat_model = _boom
    try:
        ctx = _rt(PlanContext(main_task="t", max_adaptations=3))
        _no_verdict(dr_nodes.adaptive_review(_state(_TRANSIENT), ctx))
        _no_verdict(dr_nodes.adaptive_review(_state(_EXHAUSTED, adaptations=3), ctx))
        _no_verdict(dr_nodes.adaptive_review(
            _state(_EXHAUSTED),
            _rt(PlanContext(main_task="t", adaptive_planning=False))))
        # Plan exhausted after a SUCCESSFUL last step — nothing to review.
        _no_verdict(dr_nodes.adaptive_review(_state(_OK, plan_len=1, step_index=2), ctx))
    finally:
        dr_nodes.chat_model = saved


def test_adaptive_review_does_reach_the_llm_on_exhaustion():
    """Contrast with the above: the exhaustion path is a real path, not a no-op."""
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        raise RuntimeError("stop — we only needed to prove the call happens")

    saved = dr_nodes.chat_model
    dr_nodes.chat_model = _count
    try:
        out = dr_nodes.adaptive_review(
            _state(_EXHAUSTED), _rt(PlanContext(main_task="t", max_adaptations=3)))
    finally:
        dr_nodes.chat_model = saved
    assert calls["n"] == 1
    # Fail-safe on the reasoning error: a no-adaptation verdict, so the run halts.
    assert out["adaptive_review"].needs_adaptation is False


# ── (3) what the reviewer and the replanner are told ────────────────────────


def test_exhaustion_brief_carries_the_verdict_and_the_bounces():
    brief = dr_nodes._exhaustion_brief({
        "attempts": 8, "reason": "still no Fine-Gray model",
        "unmet_requirements": [UNMET],
        "api_preflight_findings": ["lifelines.CRCSplineFitter"],
    })
    assert "8 attempt" in brief
    assert "HALTS" in brief
    assert "still no Fine-Gray model" in brief
    assert UNMET in brief
    assert "lifelines.CRCSplineFitter" in brief
    assert "fabricating" in brief, "must not invite inventing data"
    # A code-gate stop names the numerical fault instead of the unmet requirement.
    data_brief = dr_nodes._exhaustion_brief({
        "attempts": 2, "reason": "ConvergenceError",
        "data_infeasible": "non_convergence: delta contains nan",
    })
    assert "property of the DATA" in data_brief
    assert "non_convergence: delta contains nan" in data_brief
    assert "unestimable" in data_brief
    # Both prompts carry it, and are byte-identical to before when it is None.
    assert brief in adaptive_reviewer_user("s", {}, "tail", failure_block=brief)
    assert brief in adaptive_replanner_user("done", "tail", ["r"], failure_block=brief)
    assert adaptive_reviewer_user("s", {}, "tail") == adaptive_reviewer_user(
        "s", {}, "tail", failure_block=None)
    assert adaptive_replanner_user("done", "tail", ["r"]) == adaptive_replanner_user(
        "done", "tail", ["r"], failure_block=None)


# ── (4) end to end through the real graph ───────────────────────────────────


def _driver(fail_on, exhausted=True, replacement="recovery"):
    """A fake `run_step` that fails `fail_on` by exhaustion and fulfils everything
    else, plus a reviewer that adapts only after a failure and a replanner that
    proposes one replacement step."""
    executed = []
    fired = {"replan": 0}

    def run_step(state, runtime):
        n = state.get("step_index", 1)
        name = state["plan"].sub_tasks[n - 1].sub_task
        executed.append(name)
        ok = name != fail_on
        outcome = {"step_number": n, "fulfilled": ok, "attempts": 1 if ok else 3}
        if not ok:
            outcome["reason"] = "still unmet"
            if exhausted:
                outcome["exhausted"] = True
                outcome["unmet_requirements"] = [UNMET]
        return {
            "step_summaries": state.get("step_summaries", []) + [f"sum:{name}"],
            "step_outcomes": state.get("step_outcomes", []) + [outcome],
            "step_index": n + 1,
        }

    def review(state, runtime):
        failed = state["step_outcomes"][-1].get("fulfilled") is False
        return {"adaptive_review": dr_schemas.AdaptiveReview(
            needs_adaptation=failed, recommendations=["use what is installed"] if failed else [],
            reason="exhausted" if failed else "fine")}

    def replan(state, runtime):
        fired["replan"] += 1
        return {"new_tail": Plan(sub_tasks=[_step(replacement)])}

    return run_step, review, replan, executed, fired


def _run(ctx, names, driver):
    run_step, review, replan, executed, fired = driver
    saved = (dr_nodes.run_step, dr_nodes.adaptive_review, dr_nodes.replan_tail)
    dr_nodes.run_step, dr_nodes.adaptive_review, dr_nodes.replan_tail = (
        run_step, review, replan)
    try:
        final = build_deep_research_graph().invoke(
            {"plan": Plan(sub_tasks=[_step(n) for n in names]), "work_dir": None},
            context=ctx, config={"recursion_limit": 60})
    finally:
        (dr_nodes.run_step, dr_nodes.adaptive_review, dr_nodes.replan_tail) = saved
    return final, executed, fired


def test_an_exhausted_mid_plan_step_replans_instead_of_halting():
    ctx = PlanContext(main_task="t", max_adaptations=3, maximum_number_of_steps_in_plan=8)
    final, executed, fired = _run(ctx, ["s1", "s2", "s3"], _driver("s2"))
    assert fired["replan"] == 1, "the exhausted step must reach the replanner"
    # s3 is replaced by the recovery step, which then runs — the run did NOT halt at s2.
    assert executed == ["s1", "s2", "recovery"], executed
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "recovery"]
    assert len(final["adaptive_history"]) == 1
    # The failed step stays on the record: the run continued, it was not forgiven.
    assert [o["fulfilled"] for o in final["step_outcomes"]] == [True, False, True]


def test_an_exhausted_last_step_can_still_grow_a_tail():
    ctx = PlanContext(main_task="t", max_adaptations=3, maximum_number_of_steps_in_plan=8)
    final, executed, fired = _run(ctx, ["s1", "s2"], _driver("s2"))
    assert fired["replan"] == 1 and executed == ["s1", "s2", "recovery"], executed
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "recovery"]


def test_a_transient_failure_still_halts():
    ctx = PlanContext(main_task="t", max_adaptations=3)
    final, executed, fired = _run(ctx, ["s1", "s2", "s3"],
                                  _driver("s2", exhausted=False))
    assert fired["replan"] == 0 and executed == ["s1", "s2"], executed
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3"]
    assert final.get("adaptive_history", []) == []


def test_adaptive_planning_off_still_halts():
    """The router halts on its own: the driver's reviewer stub still screams "adapt!"
    (the real node early-returns — `tests/test_adaptive_disabled.py` covers that)."""
    ctx = PlanContext(main_task="t", adaptive_planning=False)
    final, executed, fired = _run(ctx, ["s1", "s2", "s3"], _driver("s2"))
    assert fired["replan"] == 0 and executed == ["s1", "s2"], executed
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3"]
    assert final.get("adaptive_history", []) == []


def test_the_replan_budget_bounds_a_step_that_keeps_exhausting():
    """An always-exhausting replacement terminates: each pass spends one adaptation."""
    ctx = PlanContext(main_task="t", max_adaptations=2,
                      maximum_number_of_steps_in_plan=8)
    # The replacement step is named like the failing one, so it exhausts again.
    final, executed, fired = _run(ctx, ["s1", "boom"],
                                  _driver("boom", replacement="boom"))
    assert fired["replan"] <= ctx.max_adaptations, fired
    assert len(final["adaptive_history"]) <= ctx.max_adaptations
    assert executed.count("boom") <= ctx.max_adaptations + 1, executed
    assert final["step_outcomes"][-1]["fulfilled"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
