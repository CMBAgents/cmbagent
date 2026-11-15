"""`claim_auditor` node + router — the anti-hallucination backstop.

After a report has ALREADY passed the goal gate, this node enumerates every
statement asserting a specific analytical RESULT about this session and grades
each against the run's clear signal (printed values, figure readings, arithmetic
reproduced from printed inputs, named source files). An `unsupported_hard` claim
sends the report back to the writer for revision; `unsupported_weak` is advisory;
method/motivation prose is `uncheckable`.

Deterministic grounding (`grounding.py`, on by default) sits AFTER the LLM:
every hard verdict is checked against a whole-token index of the run's FULL
sources. A hard claim whose numbers exist in this run's own outputs is demoted to
advisory unless the auditor cited a conflicting value of the same quantity (a
receipt) — the LLM keeps authority over meaning, never over presence. A claim
already charged this step is never re-charged (the livelock breaker): grounded →
supported, still absent → annotated without another writer round-trip.

Placement (researcher/graph.py): STRICTLY DOWNSTREAM of a FULFILLED verdict, so
the auditor never feeds the goal router, never sets `fulfilled`, and cannot
reintroduce the evaluator's fabrication-false-positive livelock. A revision
re-passes the goal gate FIRST, then the auditor runs again.

On exhaustion the report is ANNOTATED with an `## UNVERIFIED CLAIMS` block.
`claim_audit_failed` is set ONLY for a surviving hard claim that is
deterministically absent from the run's sources (and `claim_audit_halts` is
True); a grounded-but-flagged claim leaves the step fulfilled and rides to
`run_step` as advisory.

Non-fatal: any error returns `{}` and the step proceeds on its goal verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.researcher import grounding as G
from cmbagent_lg.researcher.nodes import (
    _persist_json,
    _reports_dir,
    _workspace_manifest,
    build_evidence,
    writer_node_name,
)
from cmbagent_lg.researcher.prompts import claim_auditor_instructions
from cmbagent_lg.researcher.state import ResearcherState
from cmbagent_lg.self_debug.schemas import ClaimAudit, InputSourcedClaim
from cmbagent_lg.timing import timed_node


def _auditor(ctx: PlanContext):
    # Reuse the critic (evaluator) model unless overridden.
    return chat_model(getattr(ctx, "claim_audit_model", None) or ctx.evaluator_model,
                      "critic")


def _classifier(ctx: PlanContext):
    """Model for the input-sourced forced choice. Its OWN accessor, not `_auditor`'s:
    this is a different, two-field judgement, and keeping it separate means a test can
    stub one without feeding the other's queue."""
    return chat_model(getattr(ctx, "claim_audit_model", None) or ctx.evaluator_model,
                      "critic")


_CLASSIFIER_SYS = (
    "You decide ONE thing about ONE sentence from a research report.\n\n"
    "A deterministic index has already located this sentence's numbers, and found them "
    "ONLY in a file the run was GIVEN as input - not in anything the run computed. That "
    "is either fine or a fabrication, depending on what the sentence claims to be:\n"
    "  * if it RESTATES the provided data (what the cohort contains, how many records "
    "of each kind, what a column ranges over), the numbers are correctly sourced and "
    "the sentence is a description;\n"
    "  * if it presents those numbers as a FINDING of this analysis, the analysis did "
    "not produce them and the sentence is an asserted result.\n\n"
    "Judge only this sentence, on its own words. Quote the span your choice rests on "
    "verbatim from it. When the sentence does both, or when you are unsure, answer "
    "`asserted_result` - leaving a real fabrication in place is the costlier mistake."
)


def _span_is_verbatim(span: str, statement: str) -> bool:
    """Is the quoted span really in the claim? Whitespace-insensitive, because a model
    re-wraps text; otherwise exact. A span that is not there means the classification is
    not about this sentence, so it is discarded."""
    norm = lambda t: " ".join((t or "").split()).casefold()
    a, b = norm(span), norm(statement)
    return bool(a) and len(a) >= 8 and a in b


def _demote_input_sourced_descriptions(audit, groundings: dict, ctx, state) -> list:
    """Apply the input-sourced envelope documented in `researcher/grounding.py`.

    Returns one record per DEMOTION (`[]` when none fire, which is the common case and
    costs no model call). Never raises: any failure leaves the claim hard, which is the
    behaviour this exception narrows rather than replaces.
    """
    records: list = []
    if not getattr(ctx, "input_sourced_description_demotion", True):
        return records
    try:
        eligible = G.input_only_hard_claims(audit, groundings)
    except Exception as e:  # noqa: BLE001
        print(f"[claim_auditor] input-sourced eligibility failed ({e!r}) → none",
              file=sys.stderr, flush=True)
        return records
    for claim, located in eligible:
        statement = getattr(claim, "statement", "") or ""
        # Condition 2 FIRST: deterministic, and it refuses before any model is asked.
        marker = G.asserts_a_result(statement)
        if marker:
            print(f"[claim_auditor] input-sourced claim stays HARD on the marker "
                  f"{marker!r} — not classified", file=sys.stderr, flush=True)
            continue
        # Condition 3: the forced choice, with the span verified against the text.
        try:
            verdict = _classifier(ctx).with_structured_output(InputSourcedClaim).invoke(
                [SystemMessage(_CLASSIFIER_SYS), HumanMessage(
                    "----- THE SENTENCE -----\n" + statement
                    + "\n\n----- WHERE ITS NUMBERS WERE FOUND -----\n"
                    + "\n".join(f"{v} in `{t.source}:{t.line}`" for v, t in located)
                    + "\n\nClassify it.")],
                config={"tags": ["input_sourced_classifier"]})
        except Exception as e:  # noqa: BLE001 — a failed call leaves the claim hard
            print(f"[claim_auditor] input-sourced classification failed ({e!r}) → "
                  f"claim stays HARD", file=sys.stderr, flush=True)
            continue
        kind = getattr(verdict, "kind", None)
        span = getattr(verdict, "span", "") or ""
        if kind != "description_of_provided_data":
            continue
        if not _span_is_verbatim(span, statement):
            print(f"[claim_auditor] input-sourced classification quoted a span that is "
                  f"not in the claim ({span[:60]!r}) → claim stays HARD",
                  file=sys.stderr, flush=True)
            continue
        where = "; ".join(f"{v} in `{t.source}:{t.line}`" for v, t in located)
        claim.verdict = "unsupported_weak"
        G._flag(claim)
        claim.evidence = (
            f"[input-sourced description: {where}; classified on the span "
            f"\u201c{span}\u201d — {getattr(verdict, 'justification', '') or 'no justification'}] "
            + (claim.evidence or ""))
        records.append({
            "statement": statement,
            "span": span,
            "justification": getattr(verdict, "justification", "") or "",
            "located": [{"value": v, "source": t.source, "line": t.line}
                        for v, t in located],
        })
        print(f"[claim_auditor] input-sourced description DEMOTED to advisory: "
              f"{statement[:80]!r}", file=sys.stderr, flush=True)
    return records


def _annotate(report: str, absent, advisory=()) -> str:
    """Append the machine-readable UNVERIFIED CLAIMS block to the deliverable.

    Two sub-lists, because they mean different things to a reader: `absent`
    claims carry numbers found in NO artifact this run produced; `advisory`
    claims carry numbers that DO exist in the run's outputs but that the
    auditor believes are misattributed or contradicted."""
    lines = [report.rstrip(), "", "---", "", "## UNVERIFIED CLAIMS", ""]
    if absent:
        lines += [
            "An automated grounding audit could not verify the following statements "
            "against the run's evidence (no matching printed value, figure reading, "
            "or source file). They are flagged as unverified:",
            "",
        ]
        lines += [f'- "{c.statement}" — {c.evidence}' for c in absent]
        lines.append("")
    if advisory:
        lines += [
            "The following statements use values that exist in this run's outputs, "
            "but the audit could not confirm they are attributed correctly:",
            "",
        ]
        lines += [f'- "{c.statement}" — {c.evidence}' for c in advisory]
        lines.append("")
    return "\n".join(lines)


def _hints_block(hints: Optional[List[str]]) -> str:
    """The step evaluator's `suspected_fabrications`, handed to the auditor as
    hints — spans to look at first, not verdicts."""
    hints = [h for h in (hints or []) if h and h.strip()]
    if not hints:
        return ""
    return ("\n----- SPANS THE STEP EVALUATOR COULD NOT SOURCE (hints, not verdicts) -----\n"
            + "\n".join(f"- {h}" for h in hints)
            + "\n(Check each against the QUANTITY LEDGER above before grading it.)\n"
            "---------------------------------------------")


@timed_node("researcher_claim_auditor")
def claim_auditor(
    state: ResearcherState, runtime: Runtime[PlanContext]
) -> ResearcherState:
    """Grade every result-claim in the (already goal-fulfilled) report against
    the run's clear signal, then let deterministic grounding settle presence.
    Returns the audit + budget bookkeeping; on exhaustion annotates the report
    and — for a deterministically absent claim — flags the step failed."""
    ctx = runtime.context
    report = state.get("current_report", "") or ""
    n = state.get("step_number", 1)
    if not report.strip():
        return {}  # nothing to audit → router falls through to END

    try:
        evidence = build_evidence(
            state, ctx, legacy_chars=int(getattr(ctx, "claim_audit_source_chars", 8_000)))
        ledger = evidence.ledger
        if evidence.on:
            ledger = ledger + _hints_block(state.get("claim_hints"))
        system = claim_auditor_instructions(
            ctx,
            state["step"],
            current_report=report,
            previous_steps_execution_summary=state.get("previous_steps_execution_summary") or "",
            workspace_manifest=_workspace_manifest(state.get("work_dir")),
            read_sources=evidence.stubs,
            quantity_ledger=ledger,
        )
        user = (
            "Audit the report above. Emit one entry per atomic result-claim, "
            "graded against the evidence. Cover these fields:\n\n"
            + schema_field_brief(ClaimAudit)
        )
        audit: ClaimAudit = _auditor(ctx).with_structured_output(ClaimAudit).invoke(
            [SystemMessage(system), HumanMessage(user)],
            config={"tags": ["researcher_claim_auditor"]},
        )
    except Exception as e:  # noqa: BLE001 — backstop must never break a run
        print(f"[claim_auditor] non-fatal error ({e!r}) → skipping audit",
              file=sys.stderr, flush=True)
        return {}

    seq = state.get("claim_audits_logged", 0) + 1
    accused = list(state.get("claim_accusations", []) or [])
    groundings: dict = {}
    notes = []
    if evidence.on:
        # Presence is decided here, not by the LLM: demote hard verdicts the
        # index refutes, honour receipts, and never re-charge a fingerprint
        # already charged this step.
        cm = float(G.knob(ctx, "grounding_coincidence_max"))
        groundings = G.ground_claims(audit, evidence.index, coincidence_max=cm)
        notes = G.apply_overrides(audit, groundings, evidence.index.coverage,
                                  index=evidence.index, coincidence_max=cm, accused=accused)

    # The input-sourced envelope: the one place a model may weaken a deterministic
    # finding, and only inside the conditions `grounding.py` documents. Runs after
    # `apply_overrides` (so the deterministic pass has already had its say) and before
    # `hard` is read (so a demotion changes what the step does about it).
    input_sourced = _demote_input_sourced_descriptions(audit, groundings, ctx, state)

    hard = audit.hard()

    def _fp(c) -> str:
        g = groundings.get(c.statement)
        return g.fingerprint if g is not None else G.fingerprint([], c.statement)

    def _absent(c) -> bool:
        """Deterministically absent: the index found none of the claim's numbers
        (or the claim has no numbers, where the LLM is the only judge). With
        grounding off every hard claim counts as absent — today's behaviour."""
        g = groundings.get(c.statement)
        if g is None:
            return True
        return g.deterministically_absent or g.state == "no_quantities"

    # Livelock breaker: a fingerprint already charged this step is not charged
    # again — apply_overrides has already turned a grounded repeat into
    # `supported`; a still-absent repeat is annotated here without a round-trip.
    fresh = [c for c in hard if _fp(c) not in accused]
    stale = [c for c in hard if _fp(c) in accused]
    needs_revision = bool(fresh)

    prior_attempts = state.get("claim_audit_attempts", 0)
    # Charge budget ONLY on a revision request: a clean pass — including the
    # trailing pass after a successful revise — must not disarm the guard.
    new_attempts = prior_attempts + (1 if needs_revision else 0)

    # Can the writer actually be sent the report again after this?
    can_revise = (
        needs_revision
        and new_attempts < int(getattr(ctx, "max_claim_audit_attempts", 2))
        and state.get("attempts", 0) < ctx.max_n_attempts
    )
    terminal = bool(hard) and not can_revise
    absent = [c for c in hard if _absent(c)] if terminal else []
    advisory = [c for c in hard if not _absent(c)] if terminal else []
    halts = bool(G.knob(ctx, "claim_audit_halts"))
    failed = terminal and bool(absent) and halts

    # Audit log (monotonic seq so a trailing clean pass can't overwrite the last
    # revision's record), plus the grounding record that explains every override.
    raw = state.get("work_dir")
    if raw:
        try:
            logs = Path(raw).expanduser() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / f"step_{n}_claim_audit_{seq}.json").write_text(
                json.dumps(audit.model_dump(), indent=2)
            )
        except OSError:
            pass
        if evidence.on:
            _persist_json(raw, f"step_{n}_claim_grounding_{seq}.json", {
                "seq": seq, "coverage": evidence.index.coverage.to_dict(),
                "accused_before": accused,
                "notes": [{"statement": c.statement, "action": a} for c, a in notes],
                "claims": [g.to_dict() for g in groundings.values()],
                "surviving_hard": [{"statement": c.statement, "absent": _absent(c),
                                    "repeat": _fp(c) in accused} for c in hard],
                "terminal": terminal, "failed": failed, "halts": halts,
            })

    out: ResearcherState = {
        "current_claim_audit": audit,
        "claim_audit_attempts": new_attempts,
        "claim_audits_logged": seq,
        "claim_audit_failed": failed,
        "claim_audit_revise": can_revise,
        "claim_accusations": accused + [_fp(c) for c in fresh],
    }
    if input_sourced:
        # Carried to `deep_research.run_step` → the step outcome → the status file, so
        # every demotion this exception made is auditable after the fact.
        out["input_sourced_demotions"] = input_sourced
        _persist_json(state.get("work_dir"), f"step_{n}_input_sourced_demotions.json",
                      {"step_number": n, "demotions": input_sourced})

    reports = _reports_dir(state)
    if can_revise and reports is not None:
        # Snapshot the flagged draft for the audit trail; the writer will
        # overwrite reports/step_{N}.md on the next pass. Never touch step_{N}.md
        # here (step_evaluator owns its failure-demotion).
        try:
            (reports / f"step_{n}_claimflag_{seq}.md").write_text(report)
        except OSError:
            pass

    if terminal:
        # Persistently unverifiable: annotate the deliverable. The step fails
        # only for deterministic absence (and only when halting is on).
        annotated = _annotate(report, absent, advisory)
        out["current_report"] = annotated
        if reports is not None:
            try:
                (reports / f"step_{n}.md").write_text(annotated)
            except OSError:
                pass

    overridden = sum(1 for c in audit.claims if getattr(c, "grounding_override", None))
    print(f"[claim_auditor] step {n}: {len(audit.claims)} claim(s), "
          f"{len(hard)} unsupported-hard ({len(stale)} repeat), {len(audit.weak())} advisory, "
          f"{overridden} grounding override(s) → "
          f"{'FAIL (annotated)' if failed else ('revise' if can_revise else ('flagged' if terminal else 'pass'))}",
          file=sys.stderr, flush=True)
    return out


def route_after_claim_auditor(
    state: ResearcherState, runtime: Runtime[PlanContext]
) -> str:
    """Claim gate: no surviving HARD claim → END (pass); a FRESH hard claim +
    budget left → the run's report writer (revise); otherwise END (the node has
    already annotated the report and, for deterministic absence, flagged the
    step failed). `claim_audit_revise` is the node's own answer, so a repeat
    accusation never buys another round-trip."""
    ctx = runtime.context
    audit = state.get("current_claim_audit")
    if audit is None or not audit.hard():
        return END
    if not state.get("claim_audit_revise", False):
        return END
    if state.get("claim_audit_attempts", 0) >= int(getattr(ctx, "max_claim_audit_attempts", 2)):
        return END
    if state.get("attempts", 0) >= ctx.max_n_attempts:
        return END
    return writer_node_name(ctx)


__all__ = ["claim_auditor", "route_after_claim_auditor"]
