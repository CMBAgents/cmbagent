"""The infeasibility claim is CHECKED, not believed.

`StepVerdict.infeasible_requirements` ends a step on an assertion about the environment
or the data. The bug the channel exists to fix was an evaluator getting exactly such an
assertion wrong — it demanded `sksurv.linear_model.FineGrayPHSurvivalAnalysis`, which
exists in no installed release, and refused the engineer's correct substitution eight
times. Trusting the same agent's claim that something does NOT exist reproduces the bug
with the sign flipped, and hands any model a universal bypass: echo every unmet
requirement into `infeasible_requirements` and every hard step "completes".

So `infeasibility_check.audit_declaration` resolves what the justification names against
the installed environment, looks for an output artifact the attempt actually wrote, and
refuses a declaration that covers a whole multi-bullet step whose run produced results.
Three outcomes: `verified_absent` (checked — may pass), `refuted` (contradicted — the
step fails, with the contradiction handed to the engineer), `unverified` (uncheckable —
allowed, marked, and counted against a per-plan budget).

These tests resolve REAL symbols from the installed environment rather than mocks, so
they prove the resolver is wired to reality: `scipy.optimize.curve_fit` exists,
`scipy.optimize.no_such_fine_gray_thing` does not.

Run:
    python tests/test_infeasibility_verification.py
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.persistence import analysis_status
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.infeasibility_check import (
    audit_declaration,
    candidate_symbols,
    resolve_claimed_symbols,
    unverified_budget_spent,
)
from cmbagent_lg.self_debug.schemas import (
    EngineerResponse,
    StepVerdict,
    infeasible_substitutions,
    step_goal_met,
)

# Real names in the installed environment. `curve_fit` is there; the other is not, and
# is spelled so that it never will be.
PRESENT = "scipy.optimize.curve_fit"
ABSENT = "scipy.optimize.no_such_fine_gray_thing"

BULLET = "Fit a Fine-Gray competing-risks regression for the subdistribution hazard."


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(*bullets):
    return Step(sub_task="model recurrence risk", sub_task_agent="engineer",
                bullet_points=list(bullets) or [BULLET])


def _verdict(justification, unmet=(BULLET,), declared=(BULLET,)):
    return StepVerdict(fulfilled=False, unmet_requirements=list(unmet),
                       infeasible_requirements=list(declared),
                       infeasible_justification=justification,
                       feedback="use the right model")


def _run(**kw):
    st = {"data_manifest": [], "execution_stdout": "fitted a cause-specific Cox model\n"}
    st.update(kw)
    return st


# ── (1) the resolver is wired to the real environment ────────────────────────


def test_the_resolver_answers_from_the_installed_environment():
    absent, present = resolve_claimed_symbols(
        f"there is no {ABSENT} here; {PRESENT} is what exists")
    assert absent == [ABSENT]
    assert present == [PRESENT]


def test_candidate_extraction_is_conservative():
    # A filename is not a symbol, and neither is a bare word or a version string.
    assert candidate_symbols("see data/cohort.parquet and step_5.py, version 0.28.0") == []
    assert candidate_symbols("no Fine-Gray anywhere") == []
    # An UNINSTALLED package yields no candidate: a missing package can be installed, so
    # it must never read as a verified environment absence.
    assert candidate_symbols("sksurv.linear_model.FineGrayPHSurvivalAnalysis") == []
    # A real dotted name does.
    assert candidate_symbols(f"`{PRESENT}` exists") == [PRESENT]


# ── (2) the three verdicts ───────────────────────────────────────────────────


def test_a_genuinely_absent_symbol_is_verified_and_passes():
    v = _verdict(f"No installed package implements it: {ABSENT} does not exist. "
                 f"A cause-specific Cox model was fitted instead.")
    step, run = _step(), _run()
    audit = audit_declaration(v, step, run)
    assert audit["status"] == "verified_absent"
    assert audit["absent"] == [ABSENT]
    run = {**run, "infeasibility_check": audit}
    assert infeasible_substitutions(v, step, run) == [BULLET]
    assert step_goal_met(v, step, run) is True


def test_a_symbol_that_really_exists_refutes_the_claim():
    """The irony guard: we are fixing a bug where the evaluator asserted a false fact
    about the environment, so an assertion that something is missing when it is right
    there must not end the step."""
    v = _verdict(f"This environment has no such estimator: {PRESENT} is absent.")
    step, run = _step(), _run()
    audit = audit_declaration(v, step, run)
    assert audit["status"] == "refuted" and audit["reason"] == "symbol_resolves"
    assert audit["present"] == [PRESENT]
    run = {**run, "infeasibility_check": audit}
    assert infeasible_substitutions(v, step, run) == []
    assert step_goal_met(v, step, run) is False


def test_an_unresolvable_prose_claim_is_allowed_but_marked():
    v = _verdict("the cohort has no samples with both methylation and treatment "
                 "recorded, so the comparison cannot be made")
    step, run = _step(), _run()
    audit = audit_declaration(v, step, run)
    assert audit["status"] == "unverified"
    run = {**run, "infeasibility_check": audit}
    assert infeasible_substitutions(v, step, run) == [BULLET]


def test_no_claim_means_no_audit_and_no_record():
    assert audit_declaration(StepVerdict(fulfilled=True), _step(), _run()) is None
    assert audit_declaration(
        StepVerdict(fulfilled=False, unmet_requirements=[BULLET]), _step(), _run()) is None


# ── (3) the run's own artifacts refute a declaration ─────────────────────────


def test_an_artifact_the_attempt_wrote_refutes_the_bullet_that_declared_it():
    """`declared_output_paths` in reverse: a bullet whose deliverable is on disk cannot
    be unmet, let alone impossible."""
    bullet = "Save the fold-specific hazard ratios to data/loocv_results.parquet."
    v = _verdict("the data is too sparse", unmet=(bullet,), declared=(bullet,))
    step = _step(bullet)
    run = _run(data_manifest=[{"path": "data/loocv_results.parquet", "bytes": 4032}])
    audit = audit_declaration(v, step, run)
    assert audit["status"] == "refuted" and audit["reason"] == "artifact_written"
    assert audit["artifact"] == "data/loocv_results.parquet"
    assert infeasible_substitutions(v, step, {**run, "infeasibility_check": audit}) == []

    # The same bullet with nothing written is not refuted on this ground.
    audit = audit_declaration(v, step, _run())
    assert audit["status"] == "unverified"


def test_the_engineer_is_told_what_contradicted_the_claim(tmp_path, monkeypatch):
    """A refused substitution must not come back as "it is impossible" / "try again":
    the retry carries the real import path or the file the run wrote."""

    class _Critic:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _msgs, config=None):
            return _verdict(f"no {PRESENT} in this environment")

    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())
    step = _step()
    state = {
        "step": step, "step_number": 1, "attempts": 1, "work_dir": str(tmp_path),
        "execution_stdout": "fitted a Cox model\n", "data_manifest": [],
        "current_code": EngineerResponse(python_code="print(1)", code_explanation="x"),
    }
    out = sd_nodes.step_evaluator(state, _rt(PlanContext(main_task="t", max_n_attempts=3)))
    state.update(out)

    assert out["infeasibility_check"]["status"] == "refuted"
    # The step is a goal-miss, so the loop continues as it always did.
    assert sd_nodes.route_after_step_evaluator(
        state, _rt(PlanContext(main_task="t", max_n_attempts=3))) == "engineer"
    assert len(out["step_feedback_history"]) == 1
    feedback = out["current_step_verdict"].feedback
    assert PRESENT in feedback and "INSTALLED" in feedback
    assert "use the right model" in feedback, "the evaluator's own words are kept"


def test_an_unaudited_declaration_fails_closed():
    """A caller that supplies run evidence but no audit gets a refusal, not a pass: an
    unaudited claim is the evaluator's bare word."""
    v = _verdict(f"no {ABSENT} here")
    step = _step()
    assert infeasible_substitutions(v, step, {"data_manifest": []}) == []
    assert infeasible_substitutions(v, step, {"infeasibility_check": None}) == []
    # The no-evidence form (no run_state at all) is the documented unit-test shape.
    assert infeasible_substitutions(v) == [BULLET]


# ── (4) the per-plan budget on unchecked claims ──────────────────────────────


def test_the_unverified_budget_counts_only_unchecked_claims():
    ctx = PlanContext(main_task="t")                      # default limit: 2
    unchecked = {"infeasible_requirements": ["x"], "infeasibility_status": "unverified"}
    checked = {"infeasible_requirements": ["x"], "infeasibility_status": "verified_absent"}
    assert unverified_budget_spent([], ctx) is False
    assert unverified_budget_spent([unchecked], ctx) is False
    assert unverified_budget_spent([unchecked, unchecked], ctx) is True
    # A verified absence is a fact and never counts, however many there are.
    assert unverified_budget_spent([checked] * 9, ctx) is False
    tight = PlanContext(main_task="t", max_unverified_infeasible_steps=1)
    assert unverified_budget_spent([unchecked], tight) is True


def test_a_model_declaring_everything_infeasible_halts_the_plan(monkeypatch, tmp_path):
    """The bypass the lead asked about: a model that echoes every unmet requirement into
    `infeasible_requirements` on every step. Each step passes individually — the claims
    are textually consistent and name nothing checkable — but the plan halts on the
    third, instead of producing a paper whose every hard step was declared impossible."""
    from cmbagent_lg.deep_research import nodes as dr_nodes

    bullets = ["Measure the thing.", "Report the thing."]
    plan = Plan(sub_tasks=[
        Step(sub_task=f"step {i}", sub_task_agent="engineer", bullet_points=list(bullets))
        for i in range(1, 5)])

    def _sub_state(step):
        v = StepVerdict(fulfilled=False, unmet_requirements=list(bullets),
                        infeasible_requirements=list(bullets),
                        infeasible_justification="this is simply not possible here")
        sub = {"current_step_verdict": v, "attempts": 2, "step_number": 1,
               "execution_stdout": "", "data_manifest": []}
        sub["infeasibility_check"] = audit_declaration(v, step, sub)
        return sub

    outcomes, summaries = [], []
    ctx = PlanContext(main_task="t", max_unverified_infeasible_steps=2)
    for i in range(1, 4):
        step = plan.sub_tasks[i - 1]
        monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke",
                            lambda *a, _s=step, **k: _sub_state(_s), raising=False)
        out = dr_nodes.run_step(
            {"plan": plan, "step_index": i, "work_dir": str(tmp_path),
             "step_outcomes": outcomes, "step_summaries": summaries},
            _rt(ctx))
        outcomes, summaries = out["step_outcomes"], out["step_summaries"]

    # Nothing was produced, so the over-breadth guard does not fire; the claims pass
    # individually as unverified...
    assert [o["fulfilled"] for o in outcomes] == [True, True, False]
    assert [o.get("infeasibility_status") for o in outcomes] == [
        "unverified", "unverified", "unverified_budget_spent"]
    # ...and the third is refused, with a reason that says why.
    assert "allowance of UNVERIFIED infeasibility claims" in outcomes[-1]["reason"]
    status = analysis_status(plan, outcomes)
    assert status["status"] == "halted" and status["halted_at_step"] == 3
    assert status["unverified_infeasible_steps"] == [1, 2]
    on_disk = json.loads((tmp_path / "logs" / "analysis_status.json").read_text())
    assert on_disk["unverified_infeasible_steps"] == [1, 2]


# ── (5) the fragment question ────────────────────────────────────────────────


def test_a_short_fragment_cannot_stand_for_a_long_requirement():
    """`_MIN_INFEASIBLE_OVERLAP` guards the containment rule; the audit then guards the
    claim itself. Both have to hold for a substitution to land."""
    long_bullet = ("Fit a Fine-Gray competing-risks regression for the subdistribution "
                   "hazard of recurrence, adjusting for age, grade and tumour size.")
    for fragment in ("regression", "the data", "Fine-Gray", "hazard"):
        v = StepVerdict(fulfilled=False, unmet_requirements=[long_bullet],
                        infeasible_requirements=[fragment],
                        infeasible_justification=f"no {ABSENT}")
        assert infeasible_substitutions(v) == [], fragment
    # A near-verbatim echo of the whole requirement does satisfy the textual rule — and
    # is then still subject to the audit.
    v = StepVerdict(fulfilled=False, unmet_requirements=[long_bullet],
                    infeasible_requirements=[long_bullet + " (impossible here)"],
                    infeasible_justification=f"no {PRESENT}")
    assert infeasible_substitutions(v) == [long_bullet]
    step = _step(long_bullet)
    run = _run()
    audit = audit_declaration(v, step, run)
    assert audit["status"] == "refuted", "the echo passes the text rule, not the facts"
    assert infeasible_substitutions(v, step, {**run, "infeasibility_check": audit}) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
