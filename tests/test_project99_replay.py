"""Replay of a real fleet failure on trimmed real records (tests/fixtures/project99).

What happened on the fleet: a researcher step's report cited `codebase/step_1.log`
for R² values that sit at byte ~7,600 of the 11,996-byte log — outside the 4,000-
char excerpt the graders were shown. Both graders ruled the numbers absent, the
writer was sent back eight times, the step was failed. The same audit also carried
three claims whose numbers come from the run's INPUT document
(`input_files/data_description.md`), not from anything the run computed.

The fixture holds the real `step_1.log` (one absolute path rewritten), the real
attempt-8 tools record (path made relative), the two real claim audits, the real
evaluator verdict, and the input document trimmed to its baseline table. The
replay drives the claim auditor with the recorded audits standing in for the LLM.
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
from cmbagent_lg.researcher import nodes as R
from cmbagent_lg.self_debug.schemas import ClaimAudit, StepVerdict

FIXTURE = Path(__file__).parent / "fixtures" / "project99"


@pytest.fixture
def project(tmp_path):
    """A writable copy of the fixture (the nodes write logs/ and reports/)."""
    root = tmp_path / "Iteration2"
    shutil.copytree(FIXTURE, root)
    return root / "experiment_output"


def _audit(wd, k) -> ClaimAudit:
    return ClaimAudit(**json.loads((wd / "logs" / f"step_5_claim_audit_{k}.json").read_text()))


def _report(wd, k):
    return "\n\n".join(c.statement for c in _audit(wd, k).claims)


class _AssertedResult:
    """The input-sourced classifier answering as a competent auditor does for project99:
    these sentences present their numbers as findings of the analysis."""

    def with_structured_output(self, _cls):
        return self

    def invoke(self, messages, config=None):
        from cmbagent_lg.self_debug.schemas import InputSourcedClaim

        return InputSourcedClaim(kind="asserted_result", span="variance",
                                 justification="presented as a finding of the analysis")


class _Recorded:
    def __init__(self, *audits):
        self.queue = list(audits)
        self.prompts = []

    def with_structured_output(self, cls):
        return self

    def invoke(self, messages, config=None):
        self.prompts.append("\n".join(getattr(m, "content", "") for m in messages))
        return self.queue.pop(0)


def _ctx(**kw):
    return PlanContext(main_task="Analyse the dataset", **kw)


def _rt(c):
    return types.SimpleNamespace(context=c)


def _state(wd, report, **kw):
    s = {"step": Step(sub_task="Write the results section", sub_task_agent="researcher",
                      bullet_points=["summarise every step's findings"]),
         "step_number": 5, "work_dir": str(wd), "attempts": 8, "current_report": report,
         "claim_audit_attempts": 0, "claim_audits_logged": 0, "step_feedback_history": []}
    s.update(kw)
    return s


def test_the_value_was_outside_every_old_window(project):
    log = (project / "codebase" / "step_1.log").read_text()
    assert log.index("0.9973") > 4_000                      # beyond the 4,000-char tools-log excerpt
    rec = json.loads((project / "logs" / "step_5_researcher_tools_attempt8.json").read_text())
    assert "0.9973" not in rec["sources_read"][0]["output"]  # what the graders were shown


def test_cited_claims_ground_and_input_claims_do_not(project):
    idx = G.SourceIndex.build(project, 5)
    a2 = _audit(project, 2)
    cited, homo = [c for c in a2.claims if c.verdict == "unsupported_hard"]
    g = G.ground(cited.statement, idx)
    assert g.state == "grounded" and g.cited_and_verified
    assert {q.number.text for q in g.quantities} == {"0.9973", "0.9993", "0.9898"}
    assert all(q.hits[0].token.source == "codebase/step_1.log" for q in g.quantities)
    g2 = G.ground(homo.statement, idx)
    assert g2.state == "grounded"
    assert g2.quantities[0].hits[0].token.line == 81 and "homo" in g2.quantities[0].hits[0].token.label
    a1 = _audit(project, 1)
    hard1 = [c for c in a1.claims if c.verdict == "unsupported_hard"]
    states = {c.statement[:20]: G.ground(c.statement, idx) for c in hard1}
    by = list(states.values())
    # 0.9999 / 0.9971 and 86.28 %: only in the input document
    assert by[0].state == "ungrounded" and all(q.input_only for q in by[0].quantities)
    assert by[1].state == "ungrounded" and by[1].quantities[0].input_only
    assert by[1].quantities[0].input_only[0].source.endswith("input_files/data_description.md")
    assert by[2].state in ("ungrounded", "partial")     # 90 % is input-only
    assert by[3].state == "bounds_only"                 # "typically below 0.3"


def test_replay_step_fulfils_at_audit_two(project, monkeypatch):
    a1, a2 = _audit(project, 1), _audit(project, 2)
    fake = _Recorded(a1, a2)
    monkeypatch.setattr(CA, "_auditor", lambda c: fake)
    # The input-sourced envelope would otherwise ask a model about the two unmarked
    # input-only claims. Stubbed to the honest answer, so this replay stays offline and
    # the recorded narrative (three hard claims → revise → fulfils at audit two) is
    # pinned deterministically rather than by the call happening to fail.
    # `tests/test_input_sourced_envelope.py` is where that judgement is tested.
    monkeypatch.setattr(CA, "_classifier", lambda c: _AssertedResult())
    c = _ctx(max_claim_audit_attempts=2, max_n_attempts=10)
    state = _state(project, _report(project, 1))

    out1 = CA.claim_auditor(state, _rt(c))
    hard1 = out1["current_claim_audit"].hard()
    assert [h.statement[:30] for h in hard1] == [s[:30] for s in (
        "We established that atom counts", "Even moderately complex properti",
        "whereas raw properties can be ex")]
    assert all("appears only in `input_files/data_description.md" in h.evidence for h in hard1[:2])
    assert out1["claim_audit_failed"] is False and out1["claim_audit_revise"] is True
    assert CA.route_after_claim_auditor({**state, **out1}, _rt(c)) == "react_researcher"
    bound = next(x for x in out1["current_claim_audit"].claims if "typically below" in x.statement)
    assert bound.verdict == "unsupported_weak" and bound.grounding_override
    # the stub is headed by what the researcher actually saw of the 11,996-char
    # log (the recorded excerpt; the tools record predates sha/ranges provenance)
    assert "QUANTITY LEDGER" in fake.prompts[0] and "researcher saw chars 0–3940 of 11996" in fake.prompts[0]
    assert "0.9971 — INPUT-ONLY (input_files/data_description.md" in fake.prompts[0]

    state.update(out1)
    state["attempts"] = 9
    state["current_report"] = _report(project, 2)
    out2 = CA.claim_auditor(state, _rt(c))
    audit2 = out2["current_claim_audit"]
    assert audit2.hard() == []
    demoted = [x for x in audit2.claims if x.grounding_override]
    assert len(demoted) == 2 and all(x.verdict == "unsupported_weak" for x in demoted)
    assert all("grounding override: grounded" in x.evidence for x in demoted)
    assert out2["claim_audit_failed"] is False and out2["claim_audit_revise"] is False
    # the second audit's ledger shows the graders the line the old windows cut off
    assert "0.9973 — GROUNDED exact · codebase/step_1.log:118" in fake.prompts[1]
    assert "zpve       | R2: 0.9973 | Ortho R2: 0.0018" in fake.prompts[1]
    assert CA.route_after_claim_auditor({**state, **out2}, _rt(c)) == "__end__"
    assert (project / "logs" / "step_5_claim_grounding_1.json").is_file()
    assert (project / "logs" / "step_5_claim_grounding_2.json").is_file()


def test_replay_evaluator_verdict_flips_with_the_spans(project, monkeypatch):
    """The real evaluator verdict (attempt 8) restated as the new schema: the
    unmet requirement IS the grounding doubt, the spans are found → fulfilled."""
    v = json.loads((project / "logs" / "step_5_researcher_verdict.json").read_text())
    a2 = _audit(project, 2)
    spans = [c.statement for c in a2.claims if c.verdict == "unsupported_hard"]
    verdict = StepVerdict(**v, suspected_fabrications=spans)
    fake = _Recorded(verdict)
    monkeypatch.setattr(R, "_critic", lambda c: fake)
    out = R.step_evaluator(_state(project, _report(project, 2)), _rt(_ctx()))
    assert out["current_step_verdict"].fulfilled is True
    assert out["step_feedback_history"] == []
    assert out["claim_hints"] == spans
