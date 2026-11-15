"""The input-sourced envelope: the one place a model may weaken a deterministic finding.

`apply_overrides` keeps a claim HARD when its numbers were located only in a file the run
was GIVEN. Two real fleet runs show that reading is right for one shape and wrong for the
other, with the numbers coming from the SAME file:

  * metabric Project9 step 6 — "...646 Died of Disease vs. 496 Died of Other Causes",
    quoted correctly out of `input_files/data_description.md`. A correct description of
    the provided cohort; charging it as fabrication failed five fulfilled steps and a
    finished report.
  * project99 — "We established that atom counts alone explain nearly all variance in
    total energies ($R^2 = 0.9999$)", numbers from the same file. A result the run never
    computed, asserted as its own.

Provenance cannot separate them, so a demotion needs a judgement — and a judgement from
a model is exactly what caused the bug this branch exists to fix. Hence the envelope
(documented in `researcher/grounding.py`): the numbers must be input-located, the
sentence must carry NO result-assertion marker, and a forced choice must quote a span
VERBATIM from the claim and call it a description. Any one missing leaves it hard.

These tests run against the real project99 fixture — the same records as
`test_project99_replay.py` — so they pin the envelope on real data:
  * project99's three hard claims stay hard (the lead's regression requirement);
  * claim 1 stays hard on the MARKER alone, whatever the classifier says;
  * a descriptive claim over the same input file IS demoted, and recorded;
  * a span that is not in the claim, a classifier error, and the flag off all leave it
    hard.

Run:
    python tests/test_input_sourced_envelope.py
"""

import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.researcher import claim_auditor as CA
from cmbagent_lg.researcher import grounding as G
from cmbagent_lg.self_debug.schemas import ClaimAudit, ClaimCheck, InputSourcedClaim

FIXTURE = Path(__file__).parent / "fixtures" / "project99"

# project99's three input-only hard claims, verbatim from logs/step_5_claim_audit_1.json.
ESTABLISHED = ("We established that atom counts (C, N, O, F, H) alone explain nearly all "
               "variance in total energies ($u_0$: $R^2 = 0.9999$) and zero-point "
               "vibrational energy ($zpve$: $R^2 = 0.9971$).")
DETERMINED = ("Even moderately complex properties like isotropic polarizability "
              "($\\alpha$) are 86.28% determined by stoichiometry.")
COMPONENTS = ("whereas raw properties can be explained to 90% variance by 6 components, "
              "the 12 non-redundant residuals require 8 components.")
# A metabric-shaped sentence over the SAME input file: it restates the provided
# description rather than claiming to have found anything.
DESCRIPTION = ("The provided data description lists 19 numeric columns, of which 12 "
               "remain after dropping the redundant ones.")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Iteration2"
    shutil.copytree(FIXTURE, root)
    return root / "experiment_output"


class _Classifier:
    """Stand-in for the forced choice. `answers` maps a statement prefix to the
    `InputSourcedClaim` it returns; anything unmapped answers `asserted_result`."""

    def __init__(self, answers=None, raises=False):
        self.answers = answers or {}
        self.raises = raises
        self.seen = []

    def with_structured_output(self, _cls):
        return self

    def invoke(self, messages, config=None):
        text = "\n".join(getattr(m, "content", "") for m in messages)
        self.seen.append(text)
        if self.raises:
            raise RuntimeError("provider exploded")
        for prefix, answer in self.answers.items():
            if prefix in text:
                return answer
        return InputSourcedClaim(kind="asserted_result", span="numbers",
                                 justification="presented as a finding")


def _described(span, why="it restates the provided description"):
    return InputSourcedClaim(kind="description_of_provided_data", span=span,
                             justification=why)


def _audit_of(*statements):
    return ClaimAudit(claims=[
        ClaimCheck(statement=s, kind="quantitative", verdict="unsupported_hard",
                   evidence="no matching value in the run's outputs")
        for s in statements])


def _run_envelope(project, statements, classifier, ctx=None, monkeypatch=None):
    """Drive `_demote_input_sourced_descriptions` over the real index."""
    audit = _audit_of(*statements)
    idx = G.SourceIndex.build(project, 5)
    groundings = G.ground_claims(audit, idx)
    ctx = ctx or PlanContext(main_task="Analyse the dataset")
    monkeypatch.setattr(CA, "_classifier", lambda _c: classifier)
    records = CA._demote_input_sourced_descriptions(
        audit, groundings, ctx, {"work_dir": str(project), "step_number": 5})
    return audit, records


# ── (1) the deterministic marker ─────────────────────────────────────────────


def test_the_marker_catches_the_real_asserted_result():
    assert G.asserts_a_result(ESTABLISHED) == "we established"
    # The metabric shape and project99's two unmarked claims carry none, so the marker
    # alone cannot decide them — which is why the forced choice exists.
    assert G.asserts_a_result(DESCRIPTION) is None
    assert G.asserts_a_result(DETERMINED) is None
    assert G.asserts_a_result(COMPONENTS) is None
    assert G.asserts_a_result("") is None


def test_the_marker_list_stays_closed_and_obvious():
    """A list tuned to a fixture would be a test passing itself. These are the ways a
    report says "we did this", and each must be caught in a sentence of its own."""
    for phrase in ("We found that x = 3.",
                   "Our analysis shows x = 3.",
                   "The fit confirms x = 3.",
                   "We measured x = 3.",
                   "This reveals that x = 3."):
        assert G.asserts_a_result(phrase), phrase
    # And ordinary description is not caught.
    for phrase in ("The cohort contains 646 deaths from disease.",
                   "The file lists 19 columns.",
                   "Table 1 reports a median follow-up of 7 years."):
        assert G.asserts_a_result(phrase) is None, phrase


# ── (2) project99 keeps its three hard claims ────────────────────────────────


def test_project99_keeps_all_three_hard_claims(project, monkeypatch):
    """The regression requirement: a future change that trades metabric for project99
    fails here. The classifier answers as a competent auditor would."""
    clf = _Classifier()                     # everything unmapped → asserted_result
    audit, records = _run_envelope(
        project, [ESTABLISHED, DETERMINED, COMPONENTS], clf, monkeypatch=monkeypatch)
    assert records == []
    assert [c.verdict for c in audit.claims] == ["unsupported_hard"] * 3
    # Claim 1 never reached the model: the marker refused it first.
    assert len(clf.seen) == 2
    assert not any("We established" in t for t in clf.seen)


def test_claim_one_stays_hard_whatever_the_classifier_says(project, monkeypatch):
    """The extent of the model's power, stated as a test: an adversarial classifier that
    calls everything a description still cannot touch a marked sentence."""
    clf = _Classifier({
        "We established": _described("atom counts (C, N, O, F, H)"),
        "isotropic polarizability": _described("isotropic polarizability"),
        "non-redundant residuals": _described("12 non-redundant residuals"),
    })
    audit, records = _run_envelope(
        project, [ESTABLISHED, DETERMINED, COMPONENTS], clf, monkeypatch=monkeypatch)
    by_stmt = {c.statement: c.verdict for c in audit.claims}
    assert by_stmt[ESTABLISHED] == "unsupported_hard", "the marker is not overridable"
    # The other two are the model's to decide — and every demotion is recorded.
    assert by_stmt[DETERMINED] == "unsupported_weak"
    assert by_stmt[COMPONENTS] == "unsupported_weak"
    assert len(records) == 2
    assert all(r["span"] and r["justification"] and r["located"] for r in records)


# ── (3) the metabric shape is demoted, and recorded ──────────────────────────


def test_a_description_of_provided_data_is_demoted_and_recorded(project, monkeypatch):
    span = "lists 19 numeric columns"
    clf = _Classifier({"19 numeric columns": _described(span, "it quotes the input table")})
    audit, records = _run_envelope(project, [DESCRIPTION], clf, monkeypatch=monkeypatch)

    claim = audit.claims[0]
    assert claim.verdict == "unsupported_weak"
    assert claim.grounding_override is True
    assert audit.hard() == [], "a demoted claim no longer fails the step"
    # The provenance is still FOUND and recorded — the fix reclassifies, it does not
    # stop indexing.
    assert "input_files/data_description.md" in claim.evidence
    assert span in claim.evidence and "it quotes the input table" in claim.evidence
    assert len(records) == 1 and records[0]["span"] == span
    assert {d["source"] for d in records[0]["located"]} == {"input_files/data_description.md"}


def test_the_demotion_is_persisted_and_travels_to_the_status_file(project, monkeypatch):
    """`logs/step_N_input_sourced_demotions.json` plus the outcome and the status file,
    so a human can audit what was waved through and a later analysis can count it."""
    from cmbagent_lg.deep_research import nodes as dr_nodes
    from cmbagent_lg.persistence import analysis_status
    from cmbagent_lg.planning.schemas import Plan
    from cmbagent_lg.self_debug.schemas import StepVerdict

    records = [{"statement": DESCRIPTION, "span": "lists 19 numeric columns",
                "justification": "it quotes the input table", "located": []}]
    step = Step(sub_task="write the results", sub_task_agent="researcher",
                bullet_points=["summarise"])
    plan = Plan(sub_tasks=[step])
    sub = {"current_step_verdict": StepVerdict(fulfilled=True), "attempts": 1,
           "current_report": "a report", "input_sourced_demotions": records}
    monkeypatch.setattr(dr_nodes.researcher_graph, "invoke",
                        lambda *a, **k: sub, raising=False)
    out = dr_nodes.run_step(
        {"plan": plan, "step_index": 1, "work_dir": str(project)},
        types.SimpleNamespace(context=PlanContext(main_task="t")))

    outcome = out["step_outcomes"][-1]
    assert outcome["fulfilled"] is True
    assert outcome["input_sourced_demotions"] == records
    status = analysis_status(plan, out["step_outcomes"])
    assert status["status"] == "complete"
    assert status["input_sourced_demotion_steps"] == [1]
    on_disk = json.loads((project / "logs" / "analysis_status.json").read_text())
    assert on_disk["input_sourced_demotion_steps"] == [1]


# ── (4) the envelope's other walls ───────────────────────────────────────────


def test_a_span_that_is_not_in_the_claim_is_discarded(project, monkeypatch):
    """A model that cannot point at the text it is judging is ignored."""
    clf = _Classifier({"19 numeric columns": _described("the cohort of 646 patients")})
    audit, records = _run_envelope(project, [DESCRIPTION], clf, monkeypatch=monkeypatch)
    assert records == [] and audit.claims[0].verdict == "unsupported_hard"


def test_a_trivial_span_is_discarded(project, monkeypatch):
    clf = _Classifier({"19 numeric columns": _described("19")})
    audit, records = _run_envelope(project, [DESCRIPTION], clf, monkeypatch=monkeypatch)
    assert records == [] and audit.claims[0].verdict == "unsupported_hard"


def test_a_classifier_failure_leaves_the_claim_hard(project, monkeypatch):
    clf = _Classifier(raises=True)
    audit, records = _run_envelope(project, [DESCRIPTION], clf, monkeypatch=monkeypatch)
    assert records == [] and audit.claims[0].verdict == "unsupported_hard"


def test_the_flag_off_spends_no_call_and_changes_nothing(project, monkeypatch):
    clf = _Classifier({"19 numeric columns": _described("lists 19 numeric columns")})
    ctx = PlanContext(main_task="t", input_sourced_description_demotion=False)
    audit, records = _run_envelope(project, [DESCRIPTION], clf, ctx=ctx,
                                   monkeypatch=monkeypatch)
    assert records == [] and audit.claims[0].verdict == "unsupported_hard"
    assert clf.seen == [], "the disabled path must not call the model"


def test_a_grounded_claim_is_never_eligible(project, monkeypatch):
    """The envelope only ever touches an input-only claim. A claim whose numbers the run
    actually produced is `apply_overrides`' business, not this exception's."""
    audit = _audit_of("The R-squared values were 0.9973, 0.9993 and 0.9898.")
    idx = G.SourceIndex.build(project, 5)
    groundings = G.ground_claims(audit, idx)
    assert G.input_only_hard_claims(audit, groundings) == []
    clf = _Classifier({"R-squared": _described("0.9973, 0.9993 and 0.9898")})
    monkeypatch.setattr(CA, "_classifier", lambda _c: clf)
    records = CA._demote_input_sourced_descriptions(
        audit, groundings, PlanContext(main_task="t"),
        {"work_dir": str(project), "step_number": 5})
    assert records == [] and clf.seen == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
