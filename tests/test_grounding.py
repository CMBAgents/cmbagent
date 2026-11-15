"""Deterministic claim grounding (`cmbagent_lg.researcher.grounding`).

Presence and absence of a number in a run's sources are whole-token lookups,
never an LLM's impression of an excerpt. These tests pin the tokeniser (no
substrings, locality labels, tiers), the claim-side extraction, the match and
strength rules, the verdict policy (overrides, receipts, the accusation
fingerprint breaker), the evaluator flip, the truncation markers and the
`read_file` paging footer, and the never-raises contract. Fixtures are
synthetic; nothing here is tied to any domain.
"""

import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.researcher import grounding as G
from cmbagent_lg.researcher import nodes as R
from cmbagent_lg.researcher import claim_auditor as CA
from cmbagent_lg.researcher.react_researcher import _head_tail, _make_tools, _page
from cmbagent_lg.self_debug.prompts import _head_tail as sd_head_tail
from cmbagent_lg.self_debug.schemas import ClaimAudit, ClaimCheck, StepVerdict


LOG = """# returncode=0 timed_out=False elapsed=12.3456s
--- stdout ---
Loading data from data/input.csv
Initial shape: (1200, 7)
Fitting model with seed=42 (random_state=42)
Training took 3.5 s, version 1.2.3
  alpha      | R2: 0.8787 | Ortho R2: 0.0008
  zpve       | R2: 0.9973 | Ortho R2: 0.0018
  homo       | R2: 0.1988 | Ortho R2: 0.0007
Distance correlations:
             dcor      p_value    n
res_a        0.1240    0.0310    1200
res_b        0.3420    0.0007    1200
res_c        0.5627    0.1240    1200
Summary: peak at x = 2.72, amplitude 1,234,567 counts, ratio 3.5e-4
Coverage: 75% of families converged, explained variance 63.0%
Final energy = -0.00 (converged)
--- stderr ---
warning: value 0.7777 ignored
"""

CSV = """index,family,dcor,p_value,count
0,H_C,0.4170,0.0120,84942
1,H_CO,0.5330,0.0007,16504
2,H_CN,0.086584,0.4200,3054
"""

JSON_DATA = {"S_high": {"ID": 5.59, "N": 84942}, "S_low": {"ID": 3.96, "dcor": "0.937"}}

FAILURE_LOG = "# returncode=1\n--- stdout ---\nres_q dcor 0.6161\n--- stderr ---\nTraceback\n"

IDEA = "# Idea\n\nPrior work found zpve R^2 = 0.9971 and alpha 0.8628 (86.28%).\n"


def make_workspace(tmp_path, *, with_input=True, with_figure=True) -> Path:
    wd = tmp_path / "experiment_output"
    (wd / "codebase").mkdir(parents=True)
    (wd / "data").mkdir()
    (wd / "logs").mkdir()
    (wd / "codebase" / "step_1.log").write_text(LOG)
    (wd / "codebase" / "step_1_failure_1.log").write_text(FAILURE_LOG)
    (wd / "codebase" / "step_1.py").write_text("x = 0.4242  # never indexed\n")
    (wd / "data" / "results.csv").write_text(CSV)
    (wd / "data" / "stats.json").write_text(json.dumps(JSON_DATA, indent=1))
    if with_figure:
        (wd / "logs" / "step_1_researcher_tools_attempt1.json").write_text(json.dumps({
            "attempt": 1, "sources_read": [
                {"tool": "view_image", "path": "data/fig.png", "chars": 60,
                 "output": "The peak sits at x ≈ 2.72 with amplitude 0.512 on the y axis."},
                {"tool": "read_file", "path": "codebase/step_1.log", "bytes": len(LOG),
                 "output": LOG[:200]},
            ]}))
    if with_input:
        (tmp_path / "input_files").mkdir()
        (tmp_path / "input_files" / "idea.md").write_text(IDEA)
    return wd


def index_of(tmp_path, **kw):
    return G.SourceIndex.build(make_workspace(tmp_path, **kw), 1)


def ctx(**kw):
    return PlanContext(main_task="t", **kw)


def rt(c):
    return types.SimpleNamespace(context=c)


def step():
    return Step(sub_task="Write the results", sub_task_agent="researcher",
                bullet_points=["report the fitted values"])


# ── claim-side extraction ───────────────────────────────────────────────────


def _nums(s):
    return [(n.text, n.kind, n.pct) for n in G.extract_claim_numbers(s)]


def test_extraction_latex_and_markdown_unwrap():
    ns = G.extract_claim_numbers(r"`zpve` ($R^2=0.9973$) and **u0** ($R\^2 \approx 0.9993$)")
    assert [(n.text, n.kind) for n in ns] == [("0.9973", "value"), ("0.9993", "approx")]
    assert "zpve" in ns[0].idents and "r2" in ns[0].idents


def test_extraction_thousands_percent_sci_and_pm():
    assert _nums("N = 133,885 molecules, 86.28% explained, rate 3.5e-4, 0.51 ± 0.02") == [
        ("133,885", "value", False), ("86.28%", "value", True), ("3.5e-4", "value", False),
        ("0.51", "pm", False), ("0.02", "pm", False)]
    assert _nums("about 12 percent")[0] == ("12", "approx", True)


def test_extraction_ranges_bounds_ordinals_and_sentence_end():
    assert _nums("fluctuates between 0.22 and 0.85 across bins") == [
        ("0.22", "range", False), ("0.85", "range", False)]
    ns = G.extract_claim_numbers("all sigma < 0.03 and p at least 5 with dcor 0.124.")
    assert [(n.text, n.kind, n.op) for n in ns] == [
        ("0.03", "bound", "<"), ("5", "bound", ">"), ("0.124", "value", "")]
    # ordinals / mode indices leave the denominator; N= keeps a small integer
    assert _nums("Mode 3 and the 4 families") == []
    assert _nums("N=4 families")[0][0] == "4"


def test_canonical_negative_zero_and_separators():
    assert G.canonical("-0.00") == G.canonical("0.00") == "0"
    assert G.canonical("1,234.50") == "1234.5"
    assert G.canonical("+007.10") == "7.1"
    assert G.canonical("3.5e-4") == G.canonical("0.00035")


# ── tokeniser: no substrings, locality ──────────────────────────────────────


def test_no_substring_matches(tmp_path):
    idx = index_of(tmp_path)
    # 0.086584 is a substring hit for 0.086 but NOT a rounding (it rounds to
    # 0.087): the corpus's false "rescue" that must stay ungrounded.
    assert G.ground("dcor of 0.086 for H_CN", idx).state == "ungrounded"
    assert G.ground("dcor of 0.087 for H_CN", idx).quantities[0].hits[0].match == "rounded"
    assert G.ground("dcor of 0.086584 for H_CN", idx).state == "grounded"
    # `12` never comes out of `0.124`, `1200`, `12.3456s`
    q = G.ground("The 12 residuals", idx).quantities[0]
    assert all(h.token.text != "0.124" for h in q.hits)


def test_csv_locality_labels_column_and_row(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("H_CO reaches a dcor of 0.5330", idx)
    assert g.state == "grounded"
    h = g.quantities[0].hits[0]
    assert h.token.source == "data/results.csv" and "dcor" in h.token.label and "H_CO" in h.token.label
    assert "labelled" in h.reason


def test_pandas_table_header_resolves_column_by_position(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("res_c has p_value 0.1240", idx)
    hit = g.quantities[0].hits[0]
    assert hit.token.line == 14 and "p_value" in hit.token.label and "res_c" in hit.token.label
    # the same digits sit in the dcor column of res_a: locality tells them apart
    g2 = G.ground("res_a has dcor 0.1240", idx)
    assert g2.quantities[0].hits[0].token.line == 12


def test_json_key_path_label_and_numeric_strings(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("S_high has an ID of 5.59", idx)
    assert g.state == "grounded"
    assert g.quantities[0].hits[0].token.label.startswith("S_high.ID")
    g2 = G.ground("S_low dcor 0.937", idx)
    assert g2.state == "grounded"  # a numeric string value is still a number the run wrote


def test_figure_readings_are_indexed_read_file_echoes_are_not(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("the peak amplitude is 0.512", idx)
    assert g.state in ("grounded", "weakly_grounded")
    assert g.quantities[0].hits[0].token.tier == "figure"
    cov = idx.coverage.files
    assert "logs/step_1_researcher_tools_attempt1.json" in cov
    assert not any(f.startswith("codebase/step_1.py") for f in cov)


# ── tiers, meta, input-only, cited source, self-calibration ─────────────────


def test_meta_contexts_never_ground(tmp_path):
    idx = index_of(tmp_path)
    for claim in ("the seed was 42", "it took 12.3456 s", "elapsed 3.5 s",
                  "a value of 0.7777 was observed"):
        g = G.ground(claim, idx)
        assert g.state == "ungrounded", claim
    # a dotted version is not a number at all (and `1.2` never comes out of it)
    assert G.ground("the library version 1.2.3", idx).state == "no_quantities"
    assert G.ground("a ratio of 1.2", idx).state == "ungrounded"
    # a shape tuple grounds a COUNT claim only
    assert G.ground("N=1200 molecules were used", idx).state != "ungrounded"


def test_superseded_and_code_are_excluded(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("res_q dcor 0.6161", idx)
    assert g.state == "ungrounded" and "superseded" in g.quantities[0].note
    assert G.ground("x is 0.4242", idx).state == "ungrounded"


def test_input_only_provenance(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground(r"zpve $R^2 = 0.9971$", idx)
    q = g.quantities[0]
    assert g.state == "ungrounded" and q.best == "none"
    assert q.input_only and q.input_only[0].source.endswith("input_files/idea.md")
    # percent form of an input-only fraction is input-only too, never grounded
    g2 = G.ground("alpha is 86.28% determined by size", idx)
    assert g2.state == "ungrounded" and g2.quantities[0].input_only


def test_percent_fraction_swap_only_when_labelled(tmp_path):
    idx = index_of(tmp_path)
    assert G.ground("zpve explains 99.73% of the variance", idx).state == "grounded"
    assert G.ground("something explains 99.73% of it", idx).state == "ungrounded"
    # integer 1 / 0 never ground 100% / 0%
    assert G.ground("count 100% of the families", idx).state == "ungrounded"
    # percent tokens IN the sources keep their `%` text and ground exactly, and
    # the labelled fraction form of one grounds too
    g = G.ground("75% of families converged", idx)
    assert g.state == "grounded" and g.quantities[0].hits[0].token.text == "75%"
    assert G.ground("a fraction of 0.63 of the variance was explained", idx).state != "ungrounded"
    assert G.ground("1,234,567 counts at the peak amplitude", idx).quantities[0].hits[0].match == "thousands"


def test_cited_source_rule(tmp_path):
    idx = index_of(tmp_path)
    ok = G.ground("As reported in codebase/step_1.log, zpve R2 = 0.9973 and homo R2 = 0.1988", idx)
    assert ok.cited_and_verified and ok.state == "grounded" and not ok.wrong_citation
    wrong = G.ground("As reported in data/results.csv, zpve R2 = 0.9973", idx)
    assert wrong.wrong_citation and wrong.state == "weakly_grounded"
    nowhere = G.ground("As reported in codebase/step_1.log, zpve R2 = 0.4444", idx)
    assert nowhere.state == "ungrounded"


def test_coincidence_self_calibration(tmp_path):
    wd = make_workspace(tmp_path)
    # a dense table: 4000 distinct 2-decimal values in [0, 1) → a bare 2-decimal
    # number is EXPECTED to appear by chance and is not evidence
    rows = "\n".join(f"{i / 4000:.4f}" for i in range(4000))
    (wd / "data" / "dense.csv").write_text("v\n" + rows + "\n")
    idx = G.SourceIndex.build(wd, 1)
    g = G.ground("we observed a value of 0.25 in the run", idx)
    q = g.quantities[0]
    assert q.best == "none" and "coincidence" in q.note
    # ...but the same number labelled by its column is still evidence
    assert G.ground("the v column holds 0.2500", idx).state in ("grounded", "weakly_grounded")
    # a high-specificity number is strong without a label
    assert G.ground("we found 0.086584 somewhere", idx).state == "grounded"


def test_bounds_are_checked_not_grounded(tmp_path):
    idx = index_of(tmp_path)
    g = G.ground("every dcor stays below 0.95", idx)
    assert g.state == "bounds_only" and g.bound_checks
    assert "0 violate" in g.bound_checks[0]
    g2 = G.ground("every dcor stays below 0.5", idx)
    assert "violate" in g2.bound_checks[0] and "0 violate" not in g2.bound_checks[0]


# ── verdict policy ──────────────────────────────────────────────────────────


def _audit(*pairs):
    return ClaimAudit(claims=[ClaimCheck(statement=s, kind="quantitative",
                                         verdict="unsupported_hard", evidence=e) for s, e in pairs])


def test_override_demotes_grounded_hard_to_weak(tmp_path):
    idx = index_of(tmp_path)
    audit = _audit(("zpve R2 = 0.9973", "not in the excerpt I was shown"))
    gs = G.ground_claims(audit, idx)
    notes = G.apply_overrides(audit, gs, idx.coverage, index=idx)
    c = audit.claims[0]
    assert c.verdict == "unsupported_weak" and c.grounding_override is True
    assert "grounding override" in c.evidence and notes[0][1] == "override→weak"


def test_override_stands_for_absence_input_only_and_receipt(tmp_path):
    idx = index_of(tmp_path)
    audit = _audit(
        ("zpve R2 = 0.4444", "no such value anywhere"),
        ("zpve R2 = 0.9971", "not in the log"),
        ("zpve R2 = 0.9973", "the log shows zpve R2: 0.1988, not 0.9973"),  # a receipt? no: 0.1988 is homo
        ("homo R2 = 0.9973", "the log shows homo R2: 0.1988"),              # a genuine receipt
    )
    gs = G.ground_claims(audit, idx)
    notes = dict((c.statement, a) for c, a in G.apply_overrides(audit, gs, idx.coverage, index=idx))
    c0, c1, c2, c3 = audit.claims
    assert c0.verdict == "unsupported_hard" and "NOT FOUND" in c0.evidence
    assert c1.verdict == "unsupported_hard" and "appears only in" in c1.evidence and "idea.md" in c1.evidence
    assert notes[c1.statement] == "stands: input-only"
    assert c3.verdict == "unsupported_hard" and notes[c3.statement] == "stands: receipt"
    assert "auditor cites 0.1988" in c3.evidence


def test_override_incomplete_index_never_proves_absence(tmp_path):
    idx = index_of(tmp_path)
    idx.coverage.incomplete = True
    audit = _audit(("zpve R2 = 0.4444", "nowhere"))
    G.apply_overrides(audit, G.ground_claims(audit, idx), idx.coverage, index=idx)
    assert audit.claims[0].verdict == "unsupported_weak" and "incomplete" in audit.claims[0].evidence


def test_fingerprint_breaker_repeat_becomes_supported(tmp_path):
    idx = index_of(tmp_path)
    audit = _audit(("zpve R2 = 0.9973", "log shows homo R2: 0.1988 — a contradiction"))
    gs = G.ground_claims(audit, idx)
    fp = gs["zpve R2 = 0.9973"].fingerprint
    # a rephrasing of the same numbers has the same fingerprint
    assert fp == G.ground(r"$zpve$: $R^2=0.9973$ (see above)", idx).fingerprint
    G.apply_overrides(audit, gs, idx.coverage, index=idx, accused=[fp])
    assert audit.claims[0].verdict == "supported" and audit.claims[0].grounding_override


# ── node-level policy: evaluator flip, auditor livelock breaker ─────────────


class _FakeLLM:
    """Stands in for the critic: `with_structured_output(cls).invoke(...)` returns
    the next queued object, so a node runs end to end without a model."""

    def __init__(self, *outputs):
        self.queue = list(outputs)
        self.prompts = []

    def with_structured_output(self, cls):
        return self

    def invoke(self, messages, config=None):
        self.prompts.append("\n".join(getattr(m, "content", "") for m in messages))
        return self.queue.pop(0)


def _state(wd, report, **kw):
    s = {"step": step(), "step_number": 1, "work_dir": str(wd), "attempts": 1,
         "current_report": report, "step_feedback_history": []}
    s.update(kw)
    return s


def test_evaluator_flips_grounding_only_miss_and_omits_history(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    span = "zpve reaches R2 = 0.9973 and homo R2 = 0.1988"
    verdict = StepVerdict(fulfilled=False,
                          unmet_requirements=["the R2 values for zpve and homo are not present in the log shown"],
                          feedback="remove the fabricated R2 values",
                          suspected_fabrications=[span])
    fake = _FakeLLM(verdict)
    monkeypatch.setattr(R, "_critic", lambda c: fake)
    out = R.step_evaluator(_state(wd, span), rt(ctx()))
    assert out["current_step_verdict"].fulfilled is True
    assert out["step_feedback_history"] == []          # the false call is NOT remembered
    assert out["claim_hints"] == [span] and out["source_index_key"]
    assert "QUANTITY LEDGER" in fake.prompts[0] and "suspected_fabrications" in fake.prompts[0]
    assert (wd / "logs" / "step_1_quantity_ledger_1.json").is_file()
    assert (wd / "logs" / "step_1_evaluator_grounding_1.json").is_file()


def test_evaluator_keeps_coverage_miss_and_absent_span(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    # a genuine coverage gap alongside a grounded span: no flip
    v1 = StepVerdict(fulfilled=False, unmet_requirements=["runtime was never compared"],
                     feedback="compare runtime", suspected_fabrications=["zpve R2 = 0.9973"])
    monkeypatch.setattr(R, "_critic", lambda c: _FakeLLM(v1))
    out = R.step_evaluator(_state(wd, "zpve R2 = 0.9973"), rt(ctx()))
    assert out["current_step_verdict"].fulfilled is False and len(out["step_feedback_history"]) == 1
    # a span the index does NOT find: no flip either
    v2 = StepVerdict(fulfilled=False, unmet_requirements=[], feedback="x",
                     suspected_fabrications=["zpve R2 = 0.4444"])
    monkeypatch.setattr(R, "_critic", lambda c: _FakeLLM(v2))
    out = R.step_evaluator(_state(wd, "zpve R2 = 0.4444", attempts=2), rt(ctx()))
    assert out["current_step_verdict"].fulfilled is False


def test_evaluator_grounding_off_is_legacy(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    v = StepVerdict(fulfilled=False, unmet_requirements=[], feedback="x",
                    suspected_fabrications=["zpve R2 = 0.9973"])
    fake = _FakeLLM(v)
    monkeypatch.setattr(R, "_critic", lambda c: fake)
    out = R.step_evaluator(_state(wd, "zpve R2 = 0.9973"), rt(ctx(grounding_enabled=False)))
    assert out["current_step_verdict"].fulfilled is False
    assert "claim_hints" not in out
    assert "QUANTITY LEDGER" not in fake.prompts[0] and "overrides everything above" in fake.prompts[0]


def _run_audits(wd, report, audits, c):
    """Drive claim_auditor + its router until END (the auditor LLM is patched by
    the caller); returns (outputs, routes, final state)."""
    state = _state(wd, report, attempts=1, claim_audit_attempts=0, claim_audits_logged=0)
    outs, routes = [], []
    for _ in range(10):
        out = CA.claim_auditor(state, rt(c))
        state.update(out)
        outs.append(out)
        route = CA.route_after_claim_auditor(state, rt(c))
        routes.append(route)
        if route == "__end__":
            break
        state["attempts"] += 1
    return outs, routes, state


def test_livelock_breaker_caps_audits_at_two(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    # the auditor keeps charging the same grounded number with a receipt (so the
    # override lets the first charge stand); it must not buy more than one
    # writer round-trip even with a budget of five
    charge = lambda: _audit(("homo R2 = 0.9973", "the log shows homo R2: 0.1988"))  # noqa: E731
    fake = _FakeLLM(*[charge() for _ in range(5)])
    monkeypatch.setattr(CA, "_auditor", lambda c: fake)
    outs, routes, state = _run_audits(wd, "homo R2 = 0.9973", [], ctx(max_claim_audit_attempts=5, max_n_attempts=9))
    assert routes[0] == "react_researcher" and routes[1] == "__end__"
    assert len(outs) == 2
    assert outs[1]["current_claim_audit"].claims[0].verdict == "supported"
    assert outs[1]["claim_audit_failed"] is False and outs[1]["claim_audit_revise"] is False
    assert len(state["claim_accusations"]) == 1


def test_absent_claim_fails_only_on_exhaustion_and_when_halting(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    fake = _FakeLLM(_audit(("zpve R2 = 0.4444", "nowhere")), _audit(("zpve R2 = 0.4444", "still nowhere")))
    monkeypatch.setattr(CA, "_auditor", lambda c: fake)
    outs, routes, state = _run_audits(wd, "zpve R2 = 0.4444", [], ctx(max_claim_audit_attempts=5, max_n_attempts=9))
    assert routes == ["react_researcher", "__end__"]
    assert outs[1]["claim_audit_failed"] is True
    assert "## UNVERIFIED CLAIMS" in outs[1]["current_report"] and "NOT FOUND" in outs[1]["current_report"]
    assert (wd / "logs" / "step_1_claim_grounding_2.json").is_file()
    # with halting off the same run delivers with a flag instead
    fake2 = _FakeLLM(_audit(("zpve R2 = 0.4444", "nowhere")), _audit(("zpve R2 = 0.4444", "still nowhere")))
    monkeypatch.setattr(CA, "_auditor", lambda c: fake2)
    outs, routes, state = _run_audits(wd, "zpve R2 = 0.4444", [], ctx(claim_audit_halts=False))
    assert outs[-1]["claim_audit_failed"] is False and outs[-1]["current_claim_audit"].hard()


def test_grounded_receipt_claim_is_advisory_on_exhaustion(tmp_path, monkeypatch):
    wd = make_workspace(tmp_path)
    fake = _FakeLLM(_audit(("homo R2 = 0.9973", "the log shows homo R2: 0.1988")),
                    _audit(("homo R2 = 0.9973 (kept)", "the log shows homo R2: 0.1988")))
    monkeypatch.setattr(CA, "_auditor", lambda c: fake)
    outs, routes, state = _run_audits(wd, "homo R2 = 0.9973", [], ctx(max_claim_audit_attempts=1))
    # budget of one: the first charge is already terminal → advisory, not failed
    assert routes == ["__end__"] and outs[0]["claim_audit_failed"] is False
    assert outs[0]["current_claim_audit"].hard()
    assert "attributed correctly" in outs[0]["current_report"]


# ── markers, paging, provenance fallback, never-raises, timing ──────────────


def test_truncation_markers_everywhere():
    text = "a" * 100 + "b" * 100 + "c" * 100
    cut = _head_tail(text, 100, 50, path="codebase/step_1.log")
    assert "[… 150 chars omitted (100–250 of 300); full text: codebase/step_1.log]" in cut
    assert cut.startswith("a" * 100) and cut.endswith("c" * 50)
    cut2 = sd_head_tail(text, 100, 50)
    assert "[… 150 chars omitted (100–250 of 300)]" in cut2
    assert _head_tail("short", 100, 50) == "short" and sd_head_tail("short") == "short"


def test_read_file_footer_arithmetic_and_paging(tmp_path):
    wd = make_workspace(tmp_path)
    text = "".join(f"row {i:05d} value {i * 0.001:.3f}\n" for i in range(2000))
    (wd / "data" / "big.txt").write_text(text)
    n = len(text)
    seen = []
    tools = {t.__name__: t for t in _make_tools(wd, ctx(researcher_read_page_chars=8_000), seen)}
    first = tools["read_file"]("data/big.txt")
    assert f"[showing chars 0–8000 and {n - 2000}–{n} of {n}; omitted 8000–{n - 2000} — read_file(path, offset=8000) shows them]" in first
    assert f"[… {n - 10000} chars omitted (8000–{n - 2000} of {n})]" in first
    rec = seen[-1]
    assert rec["ranges"] == [[0, 8000], [n - 2000, n]] and rec["chars"] == n
    assert rec["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert rec["excerpt"] == text[:1200] and len(rec["output"]) <= 4000
    page = tools["read_file"]("data/big.txt", offset=8000)
    assert f"[showing chars 8000–16000 of {n}; omitted 0–8000, 16000–{n} — read_file(path, offset=16000) shows the next page]" in page
    assert seen[-1]["ranges"] == [[8000, 16000]]
    last = tools["read_file"]("data/big.txt", offset=n - 100)
    assert f"[showing chars {n - 100}–{n} of {n}; omitted 0–{n - 100}]" in last
    assert "no more pages" in tools["read_file"]("data/big.txt", offset=n + 5)
    whole = tools["read_file"]("codebase/step_1.log")
    assert "[showing" not in whole and seen[-1]["ranges"] == [[0, len(LOG)]]
    # a value beyond the first page is on the page the footer points to
    assert "row 01999" not in first.split("[… ")[0] and "row 01999" in first


def test_evidence_stub_reads_from_disk_and_falls_back_on_sha_mismatch(tmp_path):
    wd = make_workspace(tmp_path)
    log = wd / "codebase" / "step_1.log"
    good = {"tool": "read_file", "path": "codebase/step_1.log", "bytes": len(LOG), "chars": len(LOG),
            "sha256": hashlib.sha256(LOG.encode()).hexdigest(), "ranges": [[0, 200]],
            "excerpt": LOG[:100], "output": LOG[:200]}
    (wd / "logs" / "step_1_researcher_tools_attempt1.json").write_text(json.dumps({"sources_read": [good]}))
    idx = G.SourceIndex.build(wd, 1)
    stubs, ledger, qs = G.render_evidence(wd, 1, "homo R2 = 0.1988", idx)
    assert "[researcher saw chars 0–200 of" in stubs and "changed since" not in stubs
    assert "0.1988 — GROUNDED exact · codebase/step_1.log:9" in ledger
    assert "homo       | R2: 0.1988" in ledger  # the neighbour line is quoted
    # the file changes under the researcher: fall back to its own excerpt, and say so
    log.write_text(LOG.replace("0.1988", "0.2000"))
    idx2 = G.SourceIndex.build(wd, 1)
    stubs2, ledger2, _ = G.render_evidence(wd, 1, "homo R2 = 0.1988", idx2)
    assert "[source changed since the researcher read it]" in stubs2
    assert "0.1988 — NOT FOUND" in ledger2


def test_never_raises(tmp_path):
    wd = make_workspace(tmp_path)
    (wd / "codebase" / "step_2.log").write_bytes(b"\x00\x01\x02" * 1000)     # binary
    (wd / "codebase" / "step_3.log").write_text("")                           # 0-byte
    (wd / "data" / "broken.json").write_text("{not json")                     # not JSON
    (wd / "data" / "huge.csv").write_text("v\n" + "1\n" * 10)
    idx = G.SourceIndex.build(wd, 1)
    assert idx.coverage.files["codebase/step_2.log"]["skipped"] == "binary"
    assert G.ground("anything 0.1988", idx).state == "grounded"
    for bad in (None, "", tmp_path / "does-not-exist", 12345):
        i = G.SourceIndex.build(bad, 1)
        assert G.ground("x = 0.5", i).state == "ungrounded" and i.coverage.n_files == 0
    assert G.extract_claim_numbers(None) == []
    assert G.ground(None, idx).state == "no_quantities"
    assert G.render_evidence(None, 1, "0.5", idx)[1] != ""
    assert G.apply_overrides(None, {}, idx.coverage) == []
    assert isinstance(G.SourceIndex.digest(tmp_path / "nope", 1), str)


def test_budget_exhaustion_marks_index_incomplete(tmp_path):
    wd = make_workspace(tmp_path)
    idx = G.SourceIndex.build(wd, 1, budget_s=0.0)
    assert idx.coverage.incomplete
    assert "INDEX INCOMPLETE" in idx.coverage.describe()


def test_index_cache_shares_one_build_per_source_digest(tmp_path):
    wd = make_workspace(tmp_path)
    a = G.index_for_step(wd, 1)
    b = G.index_for_step(wd, 1)
    assert a is b and a.key
    time.sleep(1.1)  # the digest keys on whole-second mtimes
    (wd / "data" / "extra.csv").write_text("k,v\nnew,0.6262\n")
    c = G.index_for_step(wd, 1)
    assert c is not a and G.ground("x 0.6262", c).state != "ungrounded"


@pytest.mark.slow
def test_synthetic_50mb_log_indexes_under_two_seconds(tmp_path):
    """A 50 MB log is progress spam: the same few hundred thousand lines
    repeated (epoch counters, a tqdm bar, warnings). Line dedup collapses it, so
    the index stays within the gate even though the raw file is 50 MB. Fully
    DISTINCT dense numeric lines index at ~7 MB/s in pure Python; the head/tail
    caps (16 + 4 MB) and `grounding_budget_s` bound that worst case, and an
    over-budget index is marked incomplete rather than trusted."""
    wd = make_workspace(tmp_path)
    big = wd / "codebase" / "step_2.log"
    line = "epoch {e:03d}  iter {i:05d}  loss 0.{i:06d}  acc 0.9{j:03d}  lr 1e-3\n"
    with open(big, "w") as fh:
        for i in range(0, 1_000_000):
            k = i % 50_000                       # 50k distinct lines, each repeated 20×
            fh.write(line.format(e=k % 20, i=k, j=k % 997))
    assert big.stat().st_size > 50_000_000
    t = time.perf_counter()
    idx = G.SourceIndex.build(wd, 2, budget_s=60)
    dt = time.perf_counter() - t
    assert dt < 2.0, f"index took {dt:.2f} s"
    assert G.ground("loss 0.012345 at iter 12345", idx).state == "grounded"
    t = time.perf_counter()
    qs = G.ground_report(" ".join(f"loss 0.{k:06d}" for k in range(50)), idx)
    G.render_ledger(qs, idx.coverage)
    assert time.perf_counter() - t < 0.2
