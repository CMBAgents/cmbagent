"""Infeasible-requirement channel: the evaluator can say "not possible here" instead of
failing the same step again.

`StepVerdict` had no way to express "this requirement cannot be satisfied with the
installed environment or the available data". The evaluator's only move was
`fulfilled=False` with the same unmet item, and the engineer's only move was to
resubmit. On the breast-cancer fleet eight projects spent their entire 8-attempt budget
in that loop — 30% of all attempt wall-clock — on requirements such as a Fine-Gray
competing-risks regression that no installed library implements.

`infeasible_requirements` + `infeasible_justification` carry the declaration, and
`schemas.infeasible_substitutions` adjudicates it DETERMINISTICALLY: the step completes
only when EVERY unmet requirement is declared infeasible, so a step with one impossible
and one still-fixable requirement is still a failed step. What was not delivered then
travels with the step — into the step summary (and so the report), the run outcome, and
`analysis_status.json`'s `infeasible_steps`.

Run:
    python tests/test_infeasible_channel.py
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.deep_research import nodes as dr_nodes
from cmbagent_lg.persistence import analysis_status
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.prompts import render_retry_context, step_evaluator_instructions
from cmbagent_lg.self_debug.schemas import (
    EngineerResponse,
    StepVerdict,
    infeasible_substitutions,
    step_goal_met,
)

FINE_GRAY = "fit a Fine-Gray competing-risks regression for the subdistribution hazard"
WHY = ("No installed package implements Fine-Gray; lifelines provides Cox and "
       "Kaplan-Meier only. A cause-specific Cox model was fitted instead.")


def _rt(ctx):
    return types.SimpleNamespace(context=ctx)


def _step(agent="engineer"):
    return Step(sub_task="model recurrence risk", sub_task_agent=agent,
                bullet_points=[FINE_GRAY, "print the coefficients"])


# ── (1) the adjudicator ───────────────────────────────────────────────────────


def test_old_verdicts_are_unaffected():
    """No behaviour change when the fields are absent or empty — a verdict parsed from
    an old transcript must still load and answer exactly as `fulfilled` did."""
    old = StepVerdict.model_validate_json(json.dumps({
        "fulfilled": False, "unmet_requirements": [FINE_GRAY], "feedback": "do it"}))
    assert old.infeasible_requirements == [] and old.infeasible_justification is None
    assert infeasible_substitutions(old) == []
    assert step_goal_met(old) is False
    assert step_goal_met(StepVerdict(fulfilled=True)) is True
    assert step_goal_met(None) is False
    # Any object without the fields answers "no", not an AttributeError.
    assert infeasible_substitutions(object()) == []


def test_every_unmet_declared_completes_the_step():
    v = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                    infeasible_requirements=[FINE_GRAY], infeasible_justification=WHY)
    assert infeasible_substitutions(v) == [FINE_GRAY]
    assert step_goal_met(v) is True


def test_a_partially_infeasible_step_still_fails():
    """The point of the all-or-nothing rule: an impossible requirement must not wave
    through the fixable one beside it."""
    v = StepVerdict(fulfilled=False,
                    unmet_requirements=[FINE_GRAY, "print the coefficients"],
                    infeasible_requirements=[FINE_GRAY], infeasible_justification=WHY)
    assert infeasible_substitutions(v) == []
    assert step_goal_met(v) is False


@pytest.mark.parametrize("declared", [
    FINE_GRAY,                                   # verbatim
    FINE_GRAY + ".",                             # trailing punctuation
    "  " + FINE_GRAY.upper() + "  ",             # case + whitespace
    FINE_GRAY.replace(" ", "  ") + "\n",         # collapsed whitespace
    "we cannot " + FINE_GRAY,                    # near-verbatim echo (declared ⊃ unmet)
    "Fine-Gray competing-risks regression for the subdistribution hazard",  # unmet ⊃ declared
])
def test_near_verbatim_echoes_still_match(declared):
    v = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                    infeasible_requirements=[declared])
    assert infeasible_substitutions(v) == [FINE_GRAY], declared


@pytest.mark.parametrize("declared", [
    "the data",                 # too short to stand for a long requirement
    "regression",
    "something else entirely",  # unrelated
    "",
])
def test_a_vague_declaration_does_not_cover_a_requirement(declared):
    v = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                    infeasible_requirements=[declared])
    assert infeasible_substitutions(v) == []
    assert step_goal_met(v) is False


def test_a_stray_declaration_on_a_fulfilled_verdict_is_inert():
    """The step is complete on its own terms, so there is nothing to substitute for
    and nothing to record as not delivered."""
    assert infeasible_substitutions(
        StepVerdict(fulfilled=True, infeasible_requirements=[FINE_GRAY])) == []
    assert infeasible_substitutions(StepVerdict(fulfilled=False)) == []


def test_a_moved_declaration_counts_as_the_same_statement():
    """An evaluator that MOVES the item into `infeasible_requirements` instead of
    listing it in both is making the same claim; reading an empty `unmet_requirements`
    as "nothing declared" would silently drop the channel."""
    v = StepVerdict(fulfilled=False, unmet_requirements=[],
                    infeasible_requirements=[FINE_GRAY], infeasible_justification=WHY)
    assert infeasible_substitutions(v) == [FINE_GRAY]
    assert step_goal_met(v) is True


# The two verdicts the replay harness injects, verbatim from the fleet
# (cases/case{1,2}_*/inputs/infeasible_verdict.json, themselves derived from
# logs/step_{4,5}_verdict_failure_8.json). A refactor that stops accepting these
# re-breaks metabric Project3/Iteration1 step 4 and Project1/Iteration2 step 5.
_METABRIC_FINE_GRAY = {
    "fulfilled": False,
    "unmet_requirements": [
        "Fit Fine-Gray competing risks models using scikit-survival "
        "(OS_MONTHS, target 1 vs 2)."],
    "feedback": "You have repeatedly failed to implement the Fine-Gray competing "
                "risks models using 'scikit-survival' as explicitly requested.",
    "suspected_fabrications": [],
    "infeasible_requirements": [
        "Fit Fine-Gray competing risks models using scikit-survival "
        "(OS_MONTHS, target 1 vs 2)."],
    "infeasible_justification":
        "scikit-survival 0.28.0 has no Fine-Gray subdistribution regression and "
        "lifelines 0.30.3 has none either; the API pre-flight proved both absent. "
        "Cause-specific Cox models were fitted per event type instead.",
}
_METABRIC_SMD = {
    "fulfilled": False,
    "unmet_requirements": [
        "The Love plot must demonstrate that post-weighting SMDs are below 0.1."],
    "feedback": "The IPTW analysis was performed, but the covariate balance check "
                "failed to achieve the goal of bringing all SMDs below 0.1.",
    "suspected_fabrications": [],
    "infeasible_requirements": [
        "The Love plot must demonstrate that post-weighting SMDs are below 0.1."],
    "infeasible_justification":
        "The bullet mandates a statistical OUTCOME, not a computation. ... no "
        "propensity model can be required to produce a prescribed number.",
}


@pytest.mark.parametrize("raw,needle", [
    (_METABRIC_FINE_GRAY, "Fine-Gray"),
    (_METABRIC_SMD, "SMDs are below 0.1"),
])
def test_the_real_fleet_verdicts_are_accepted(raw, needle):
    v = StepVerdict.model_validate(raw)
    subs = infeasible_substitutions(v)
    assert len(subs) == 1 and needle in subs[0], subs
    assert step_goal_met(v) is True


# ── (1b) the over-breadth guard: the channel is a trust grant, not a free pass ──


_SIX_BULLETS = [
    "Read the processed clinical data and merge on SAMPLE_ID.",
    "Check for collinearity between the new clusters and INTCLUST.",
    "Fit cause-specific Cox models with lifelines, one per event type.",
    "Test for interaction between clusters and INTCLUST.",
    "Compute and plot Aalen-Johansen cumulative incidence functions.",
    "Save the CIF plots and the Cox summaries flat in data/.",
]


def _six_bullet_step():
    return Step(sub_task="Competing Risks Survival Analysis",
                sub_task_agent="engineer", bullet_points=list(_SIX_BULLETS))


def _produced():
    """A run that demonstrably delivered: files written and results printed."""
    return {"data_manifest": [{"path": "data/cif.png", "bytes": 1}],
            "execution_stdout": "cross-tab printed; both Cox models fitted\n"}


def _audited(verdict, step, run_state):
    """`run_state` with the engine's own audit of the claim attached, exactly as
    `step_evaluator` leaves it. Evidence guards live in `infeasibility_check`; the
    adjudicator only reads the result, and a declaration with no audit fails closed."""
    from cmbagent_lg.self_debug.infeasibility_check import audit_declaration

    return {**run_state,
            "infeasibility_check": audit_declaration(verdict, step, run_state)}


def test_declaring_every_bullet_infeasible_buys_nothing():
    """The adversarial reading: an evaluator could escape any loop by calling the whole
    step impossible. Probed in the replay harness (case5, phase `over_declared`): all
    six bullets declared on the words "The data is too sparse", while the run's own
    stdout and manifest show five of them delivered."""
    v = StepVerdict(fulfilled=False, unmet_requirements=list(_SIX_BULLETS),
                    infeasible_requirements=list(_SIX_BULLETS),
                    infeasible_justification="The data is too sparse.")
    step, run = _six_bullet_step(), _produced()
    run = _audited(v, step, run)
    assert run["infeasibility_check"]["reason"] == "over_broad"
    assert infeasible_substitutions(v, step, run) == []
    assert step_goal_met(v, step, run) is False
    # The no-evidence form applies the textual rule alone — that is what the audit is
    # for, and `_evidence_refuses` is what makes an unaudited claim fail closed.
    assert infeasible_substitutions(v) == list(_SIX_BULLETS)


def test_one_bullet_of_six_is_the_honest_case():
    """The shape the channel exists for: five delivered, one impossible."""
    v = StepVerdict(fulfilled=False,
                    unmet_requirements=["Test for interaction between clusters and INTCLUST."],
                    infeasible_requirements=["Test for interaction between clusters and INTCLUST."],
                    infeasible_justification="the interaction design is rank-deficient")
    step, run = _six_bullet_step(), _produced()
    run = _audited(v, step, run)
    assert len(infeasible_substitutions(v, step, run)) == 1
    assert step_goal_met(v, step, run) is True


def test_a_single_bullet_step_can_still_declare_its_only_requirement():
    """You cannot over-claim on a one-item list, and the fleet's SMD-threshold step is
    exactly that shape — the guard must not touch it."""
    bullet = "The Love plot must demonstrate that post-weighting SMDs are below 0.1."
    v = StepVerdict(fulfilled=False, unmet_requirements=[bullet],
                    infeasible_requirements=[bullet],
                    infeasible_justification="three of twenty SMDs stay above 0.1")
    step = Step(sub_task="assess balance", sub_task_agent="engineer",
                bullet_points=[bullet])
    run = _audited(v, step, _produced())
    assert infeasible_substitutions(v, step, run) == [bullet]
    assert step_goal_met(v, step, run) is True


def test_a_run_that_produced_nothing_is_not_second_guessed():
    """With nothing delivered, "none of this was possible" does not contradict the
    evidence — and the step fails at the goal gate anyway (a silent run is never
    fulfilled), so refusing the substitution would change nothing."""
    v = StepVerdict(fulfilled=False, unmet_requirements=list(_SIX_BULLETS),
                    infeasible_requirements=list(_SIX_BULLETS),
                    infeasible_justification="The data is too sparse.")
    step = _six_bullet_step()
    empty = _audited(v, step, {"data_manifest": [], "execution_stdout": "   \n"})
    assert infeasible_substitutions(v, step, empty) == list(_SIX_BULLETS)


# ── (2) the channel is advertised to the evaluator and the engineer ───────────


def test_the_evaluator_is_told_the_channel_exists():
    ctx = PlanContext(main_task="t")
    system = step_evaluator_instructions(
        ctx, _step(), stdout="x", data_manifest=[], step_feedback_history=[])
    assert "infeasible_requirements" in system
    assert "infeasible_justification" in system
    assert "EVERY item in `unmet_requirements`" in system, \
        "the all-or-nothing rule must be stated, not just implied"
    # A researcher step (the schema is shared) is not offered the channel.
    prose = step_evaluator_instructions(
        ctx, _step("researcher"), stdout="x", data_manifest=[], step_feedback_history=[])
    assert "infeasible_requirements" not in prose
    # The field brief the node appends to the user turn carries the descriptions.
    brief = schema_field_brief(StepVerdict)
    assert "infeasible_requirements" in brief and "Engineer steps only" in brief


def test_a_partial_verdict_tells_the_engineer_what_not_to_retry():
    """The only path where the engineer sees these: a PARTIAL declaration still
    retries, and must not spend the attempt re-arguing the impossible half."""
    block = render_retry_context(
        attempts=2, max_attempts=3, last_code="print(1)", last_stderr=None,
        last_stdout="ok", last_returncode=0, last_timed_out=False,
        last_fix_suggestion=None, error_history=[],
        last_step_unmet=[FINE_GRAY, "print the coefficients"],
        last_step_feedback="print them",
        last_step_infeasible=[FINE_GRAY],
    )
    assert "IMPOSSIBLE in this environment" in block
    assert block.count(FINE_GRAY) == 2, "listed as unmet AND as impossible"
    # Absent → byte-identical to before the field existed.
    kwargs = dict(
        attempts=2, max_attempts=3, last_code="print(1)", last_stderr=None,
        last_stdout="ok", last_returncode=0, last_timed_out=False,
        last_fix_suggestion=None, error_history=[], last_step_unmet=[FINE_GRAY],
        last_step_feedback="do it")
    assert render_retry_context(**kwargs) == render_retry_context(
        **kwargs, last_step_infeasible=None)


# ── (3) the goal gate ends the step instead of retrying it ───────────────────


class _Critic:
    """Minimal stand-in for the structured-output critic."""

    def __init__(self, verdict):
        self.verdict = verdict

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _msgs, config=None):
        return self.verdict


def _run_step_evaluator(monkeypatch, tmp_path, verdict, attempts=1):
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic(verdict))
    codebase = tmp_path / "codebase"
    codebase.mkdir(parents=True, exist_ok=True)
    (codebase / "step_1.py").write_text("print(1)\n")
    (codebase / "step_1.log").write_text("# returncode=0\n")
    state = {
        "step": _step(),
        "step_number": 1,
        "attempts": attempts,
        "work_dir": str(tmp_path),
        "execution_stdout": "cause-specific Cox fitted\n",
        "data_manifest": [],
        "current_code": EngineerResponse(python_code="print(1)", code_explanation="x"),
    }
    out = sd_nodes.step_evaluator(state, _rt(PlanContext(main_task="t", max_n_attempts=3)))
    state.update(out)
    return state, out


def test_a_fully_infeasible_step_ends_without_another_attempt(monkeypatch, tmp_path):
    verdict = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                          infeasible_requirements=[FINE_GRAY],
                          infeasible_justification=WHY)
    state, out = _run_step_evaluator(monkeypatch, tmp_path, verdict)

    ctx = PlanContext(main_task="t", max_n_attempts=3)
    # Attempts remain, and the old gate would have gone back to the engineer.
    assert state["attempts"] < ctx.max_n_attempts
    assert sd_nodes.route_after_step_evaluator(state, _rt(ctx)) == END

    # Accepted, so it is NOT recorded as a goal-miss: no feedback trail, and the
    # canonical script/log keep their names.
    assert out["step_feedback_history"] == []
    assert (tmp_path / "codebase" / "step_1.py").exists()
    assert not (tmp_path / "codebase" / "step_1_failure_1.py").exists()
    # The verdict is still on disk in full, substitution included.
    saved = json.loads((tmp_path / "logs" / "step_1_verdict.json").read_text())
    assert saved["infeasible_requirements"] == [FINE_GRAY]
    assert not (tmp_path / "logs" / "step_1_verdict_failure_1.json").exists()


def test_a_partial_verdict_still_goes_back_to_the_engineer(monkeypatch, tmp_path):
    verdict = StepVerdict(fulfilled=False,
                          unmet_requirements=[FINE_GRAY, "print the coefficients"],
                          infeasible_requirements=[FINE_GRAY],
                          infeasible_justification=WHY, feedback="print them")
    state, out = _run_step_evaluator(monkeypatch, tmp_path, verdict)
    ctx = PlanContext(main_task="t", max_n_attempts=3)
    assert sd_nodes.route_after_step_evaluator(state, _rt(ctx)) == "engineer"
    assert len(out["step_feedback_history"]) == 1
    assert (tmp_path / "codebase" / "step_1_failure_1.py").exists()
    assert (tmp_path / "logs" / "step_1_verdict_failure_1.json").exists()


# ── (4) the substitution reaches the outcome, the report and the status file ──


def _fake_sub_state(verdict, step=None):
    """What the sub-graph hands back, including the audit `step_evaluator` computed."""
    from cmbagent_lg.self_debug.infeasibility_check import audit_declaration

    sub = {
        "current_step_verdict": verdict,
        "attempts": 2,
        "escalated": False,
        "step_number": 1,
        "current_code": EngineerResponse(python_code="print(1)", code_explanation="x"),
        "execution_stdout": "cause-specific Cox fitted\n",
    }
    sub["infeasibility_check"] = audit_declaration(verdict, step, sub)
    return sub


def _run_one_step(monkeypatch, verdict, agent="engineer", work_dir=None):
    """Drive the real `run_step` with the sub-graph replaced by a canned sub-state."""
    step = Step(sub_task="model recurrence risk", sub_task_agent=agent,
                bullet_points=[FINE_GRAY])
    plan = Plan(sub_tasks=[step])
    sub = _fake_sub_state(verdict, step)
    monkeypatch.setattr(dr_nodes.self_debug_graph, "invoke",
                        lambda *a, **k: sub, raising=False)
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke",
                        lambda *a, **k: sub, raising=False)
    out = dr_nodes.run_step(
        {"plan": plan, "step_index": 1, "work_dir": work_dir},
        _rt(PlanContext(main_task="t")))
    return plan, out


def test_outcome_summary_and_status_record_the_substitution(monkeypatch, tmp_path):
    verdict = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                          infeasible_requirements=[FINE_GRAY],
                          infeasible_justification=WHY)
    plan, out = _run_one_step(monkeypatch, verdict, work_dir=str(tmp_path))

    outcome = out["step_outcomes"][-1]
    assert outcome["fulfilled"] is True, "an all-infeasible step completes"
    assert outcome["infeasible_requirements"] == [FINE_GRAY]
    assert outcome["infeasible_justification"] == WHY
    assert "reason" not in outcome

    # The step summary is what later steps and the report see.
    summary = out["step_summaries"][-1]
    assert "NOT DELIVERED" in summary and FINE_GRAY in summary and WHY in summary

    # analysis_status.json: complete, but the step is listed.
    status = analysis_status(plan, out["step_outcomes"])
    assert status["status"] == "complete" and status["all_fulfilled"] is True
    assert status["infeasible_steps"] == [1]
    # This justification names no resolvable symbol, so the claim is recorded as
    # asserted rather than checked — and the summary says so in as many words.
    assert outcome["infeasibility_status"] == "unverified"
    assert status["unverified_infeasible_steps"] == [1]
    assert "UNVERIFIED infeasibility claim" in summary
    on_disk = json.loads((tmp_path / "logs" / "analysis_status.json").read_text())
    assert on_disk["infeasible_steps"] == [1]
    assert on_disk["unverified_infeasible_steps"] == [1]


def test_nothing_is_recorded_on_an_ordinary_pass(monkeypatch, tmp_path):
    plan, out = _run_one_step(monkeypatch, StepVerdict(fulfilled=True),
                              work_dir=str(tmp_path))
    outcome = out["step_outcomes"][-1]
    assert outcome["fulfilled"] is True
    assert "infeasible_requirements" not in outcome
    assert "NOT DELIVERED" not in out["step_summaries"][-1]
    assert analysis_status(plan, out["step_outcomes"])["infeasible_steps"] == []


def test_a_researcher_step_cannot_use_the_channel(monkeypatch):
    """The schema is shared with the researcher's own evaluator; the channel is not."""
    verdict = StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                          infeasible_requirements=[FINE_GRAY],
                          infeasible_justification=WHY)
    plan, out = _run_one_step(monkeypatch, verdict, agent="researcher")
    outcome = out["step_outcomes"][-1]
    assert outcome["fulfilled"] is False
    assert "infeasible_requirements" not in outcome
    assert analysis_status(plan, out["step_outcomes"])["status"] == "halted"


# ── (5) end to end: the real graph spends ONE attempt, not the whole budget ──


SCRIPT = """import sys

if __name__ == "__main__":
    print("cause-specific Cox fitted; Fine-Gray unavailable in this environment")
"""


def test_the_graph_stops_after_one_attempt(tmp_path, monkeypatch):
    """The measured failure mode, end to end: with an 8-attempt budget the old gate
    sent the step back 7 more times. The declaration ends it on the first."""
    import types as _types

    from cmbagent_lg.self_debug.graph import graph
    from cmbagent_lg.self_debug.schemas import ExecutionVerdict

    monkeypatch.delenv("CMBAGENT_LG_API_PREFLIGHT", raising=False)
    monkeypatch.delenv("CMBAGENT_LG_REMOTE_EXECUTOR", raising=False)

    prompts = []

    class _Proposer:
        def invoke(self, msgs, config=None):
            prompts.append(msgs[0].content)
            return _types.SimpleNamespace(text=SCRIPT)

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

        def invoke(self, _msgs, config=None):
            if self.schema is ExecutionVerdict:
                return ExecutionVerdict(status="success")
            return StepVerdict(fulfilled=False, unmet_requirements=[FINE_GRAY],
                               infeasible_requirements=[FINE_GRAY],
                               infeasible_justification=WHY,
                               feedback="not available here")

    monkeypatch.setattr(sd_nodes, "_proposer", lambda _c: _Proposer())
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: _Formatter())
    monkeypatch.setattr(sd_nodes, "_critic", lambda _c: _Critic())

    ctx = PlanContext(main_task="t", enable_preflight=False, enable_intent_gate=False,
                      vlm_enabled=False, max_n_attempts=8)
    out = graph.invoke({"step": _step(), "step_number": 1, "work_dir": str(tmp_path)},
                       context=ctx)

    assert len(prompts) == 1, f"the step was retried {len(prompts) - 1} time(s)"
    assert out["attempts"] == 1
    assert out["step_feedback_history"] == []
    assert step_goal_met(out["current_step_verdict"]) is True
    assert out["current_step_verdict"].fulfilled is False, \
        "the verdict is unchanged; only the GATE's reading of it differs"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
