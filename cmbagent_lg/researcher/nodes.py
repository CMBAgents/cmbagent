"""Researcher + researcher_step_evaluator nodes for the researcher sub-graph.

  - `researcher` writes a markdown report (raw `.invoke()` — no structured
    output; the report content IS the deliverable), persisted to
    `reports/step_{N}.md`.
  - `step_evaluator` reads that report and emits a `StepVerdict`.

A goal-miss demotes the canonical `reports/step_{N}.md` to
`reports/step_{N}_failure_{attempt}.md` so the audit trail covers every attempt.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model, invoke_or_end as _invoke_or_end
from cmbagent_lg.prompt_utils import flatten_content, schema_field_brief
from cmbagent_lg.vlm import collect_images, with_images
from cmbagent_lg.researcher import grounding as G
from cmbagent_lg.researcher.prompts import (
    render_retry_context,
    researcher_instructions,
    step_evaluator_instructions,
)
from cmbagent_lg.researcher.state import ResearcherState
from cmbagent_lg.self_debug.schemas import StepVerdict
from cmbagent_lg.timing import timed_node


# Per-role chat models from the run context (None → llms._DEFAULT_MODEL): here
# the "generator" is the researcher and the "critic" is the evaluator.
def _proposer(ctx: PlanContext):
    return chat_model(ctx.researcher_model, "generator")


def _critic(ctx: PlanContext):
    return chat_model(ctx.evaluator_model, "critic")


def _workspace_manifest(work_dir) -> str:
    """The WORKSPACE FILE MANIFEST for the step evaluator's grounding check.

    Delegates to deep_research's builder so the evaluator sees the same listing
    the researcher was given. Imported lazily — `deep_research.nodes` imports
    this package's graph, so a module-level import would be circular. Never
    raises: without a manifest the evaluator loses one grounding signal.
    """
    if not work_dir:
        return ""
    try:
        from cmbagent_lg.deep_research.nodes import _build_workspace_manifest
        return _build_workspace_manifest(str(work_dir))
    except Exception:  # noqa: BLE001
        return ""


# Per-source excerpt handed to the evaluator: enough to check a parameter table
# or figure description, capped so a big CSV can't dominate the prompt.
_EVAL_SOURCE_CHARS = 2_500


def _read_sources(work_dir, step_number: int, max_chars: int = _EVAL_SOURCE_CHARS) -> str:
    """What the ReAct researcher actually read, from its tool audit log — the
    evidence a grader needs to tell a file-sourced number from an invented one.

    `max_chars` caps each per-source excerpt: the claim_auditor passes a larger
    window than the step_evaluator so per-claim matching isn't starved (a value
    truncated away must read as WEAK, never as a false HARD).

    Empty for the plain researcher, which reads nothing — there an unsourced
    number IS unsupported. Never raises.
    """
    if not work_dir:
        return ""
    try:
        logs = Path(work_dir).expanduser() / "logs"
        # Union the reads across ALL attempts, not just the last: on a retry the
        # researcher does NOT re-read what the handed-back draft already contains,
        # so the last attempt's audit alone under-reports grounding and the
        # evaluator rejects a correctly-sourced report as fabricated (a livelock).
        # Fall back to the canonical file for the plain researcher / older runs.
        per_attempt = sorted(
            logs.glob(f"step_{step_number}_researcher_tools_attempt*.json"))
        files = per_attempt or [logs / f"step_{step_number}_researcher_tools.json"]
        by_path: dict = {}  # path -> the richest read of it across attempts
        for f in files:
            if not f.is_file():
                continue
            for s in json.loads(f.read_text()).get("sources_read", []):
                p = s.get("path", "?")
                if p not in by_path or \
                        len(s.get("output") or "") > len(by_path[p].get("output") or ""):
                    by_path[p] = s
        sources = list(by_path.values())
        if not sources:
            return ""
        blocks = []
        for s in sources:
            body = (s.get("output") or "").strip()
            if not body:
                continue
            name = Path(s.get("path", "?")).name
            verb = "read from" if s.get("tool") == "read_file" else "read off the figure"
            blocks.append(f"**{verb} {name}:**\n```\n{body[:max_chars]}\n```")
        if not blocks:
            return ""
        return (
            "----- ARTIFACTS THE RESEARCHER READ ----------\n"
            "The researcher opened these artifacts while writing the report, and\n"
            "this is what they contained. A value that appears here IS sourced —\n"
            "check the report's numbers against them.\n\n"
            + "\n\n".join(blocks)
            + "\n---------------------------------------------"
        )
    except Exception:  # noqa: BLE001
        return ""


class Evidence:
    """What a grader is shown about the run's artifacts, plus the index behind it.

    `stubs` = the ARTIFACTS THE RESEARCHER READ block (short heads, each headed
    by the ranges the researcher actually saw); `ledger` = the QUANTITY LEDGER
    (one line per report number: where it exists in the FULL sources, or that
    it does not); `groundings` = the ledger's records; `index` = the per-step
    `SourceIndex` the auditor and the override reuse. With grounding off,
    `stubs` is the legacy excerpt block and the rest is empty."""

    def __init__(self, stubs="", ledger="", groundings=None, index=None):
        self.stubs, self.ledger, self.groundings, self.index = stubs, ledger, groundings or [], index

    @property
    def on(self) -> bool:
        return self.index is not None


def build_evidence(state: ResearcherState, ctx: PlanContext, *, legacy_chars: int) -> Evidence:
    """The grader evidence for this attempt. Never raises: a grounding failure
    degrades to the legacy excerpt block, never to no evidence."""
    work_dir = state.get("work_dir")
    n = state.get("step_number", 1)
    if not G.knob(ctx, "grounding_enabled"):
        return Evidence(_read_sources(work_dir, n, max_chars=legacy_chars))
    try:
        index = G.index_for_step(work_dir, n, ctx)
        stubs, ledger, qs = G.render_evidence(
            work_dir, n, state.get("current_report", "") or "", index,
            ledger_chars=int(G.knob(ctx, "evidence_ledger_chars")),
            stub_chars=min(1_200, int(legacy_chars)),
            coincidence_max=float(G.knob(ctx, "grounding_coincidence_max")),
        )
        return Evidence(stubs, ledger, qs, index)
    except Exception as e:  # noqa: BLE001
        print(f"[grounding] evidence build failed ({e!r}) → legacy excerpts",
              file=sys.stderr, flush=True)
        return Evidence(_read_sources(work_dir, n, max_chars=legacy_chars))


def _persist_json(work_dir, name: str, payload) -> None:
    """Best-effort write under `logs/`; a log must never break a run."""
    if not work_dir:
        return
    try:
        logs = Path(work_dir).expanduser() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / name).write_text(json.dumps(payload, indent=2, default=str))
    except Exception:  # noqa: BLE001
        pass


# Vocabulary of a presence complaint. An unmet requirement that uses it AND
# names an identifier of a suspected span is the grounding doubt restated as a
# coverage failure — the exact phrasing the fleet's evaluator used ("R² values
# for u0, zpve, … that are not present in the provided step_1.log").
_PRESENCE_WORDS = re.compile(
    r"not (?:present|found|contain|in the|supported|appear|listed|shown)|does not contain|"
    r"fabricat|invent|unsourced|no evidence|made up|absent from|truncated|not backed|"
    r"cannot be (?:found|verified|located)|could not (?:be )?(?:find|found|verif|locat)", re.I)


def _refers_to(unmet: str, spans: List[str]) -> bool:
    """Does an unmet requirement point at one of the suspected spans (rather
    than at a coverage gap)? True when they share a number, or a run of six
    words, or one contains the other, or the requirement is a presence
    complaint naming an identifier the span carries. Deterministic on
    purpose: this decides whether a goal-miss was really a grounding doubt in
    disguise, and a coverage gap phrased without any of those never flips."""
    u = (unmet or "").strip().lower()
    if not u:
        return True
    u_nums = {n.canonical for n in G.extract_claim_numbers(u)}
    u_words = re.findall(r"[a-z0-9_]+", u)
    u_shingles = {" ".join(u_words[i:i + 6]) for i in range(max(0, len(u_words) - 5))}
    u_idents = {w for w in G._words(u) if len(w) >= 2 and w not in G._STOPWORDS}
    presence = bool(_PRESENCE_WORDS.search(u))
    for sp in spans:
        t = (sp or "").strip().lower()
        if not t:
            continue
        if u in t or t in u:
            return True
        if u_nums and u_nums & {n.canonical for n in G.extract_claim_numbers(t)}:
            return True
        w = re.findall(r"[a-z0-9_]+", t)
        if u_shingles & {" ".join(w[i:i + 6]) for i in range(max(0, len(w) - 5))}:
            return True
        if presence:
            span_idents = {n_id for cn in G.extract_claim_numbers(t) for n_id in cn.idents[:8]}
            if u_idents & span_idents:
                return True
    return False


def grounding_flip(verdict: StepVerdict, evidence: Evidence, ctx: PlanContext):
    """The step-evaluator rule: a goal-miss that is ONLY a grounding doubt, over
    spans whose numbers the index finds in this run's outputs, is flipped to
    fulfilled and the spans become hints for the claim auditor.

    Returns (flipped, hints, records). Never flips a genuine coverage failure:
    an `unmet_requirements` entry that does not refer to a suspected span keeps
    the miss. Never raises."""
    try:
        spans = [s for s in (getattr(verdict, "suspected_fabrications", None) or []) if s and s.strip()]
        if verdict.fulfilled or not evidence.on:
            return False, spans, []
        if not spans:
            return False, [], []
        if any(not _refers_to(u, spans) for u in (verdict.unmet_requirements or [])):
            return False, spans, []
        cm = float(G.knob(ctx, "grounding_coincidence_max"))
        gs = [G.ground(sp, evidence.index, coincidence_max=cm) for sp in spans]
        records = [g.to_dict() for g in gs]
        if all(g.rescuable and not g.wrong_citation for g in gs):
            verdict.fulfilled = True
            verdict.unmet_requirements = []
            return True, spans, records
        return False, spans, records
    except Exception as e:  # noqa: BLE001
        print(f"[grounding] evaluator flip error ({e!r})", file=sys.stderr, flush=True)
        return False, [], []


def _reports_dir(state: ResearcherState) -> Optional[Path]:
    """`{work_dir}/reports/` — markdown reports live here. None if no work_dir."""
    raw = state.get("work_dir")
    if not raw:
        return None
    out = Path(raw).expanduser() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    return out


def claim_retry_feedback(state: ResearcherState):
    """(unsupported_spans, suggestions) when this retry was triggered by the
    claim_auditor — i.e. the prior verdict was FULFILLED but a claim was
    unsupported — else (None, None). Shared by both report writers so a claim
    revision is fed back identically whichever writer the run uses."""
    prior_verdict = state.get("current_step_verdict")
    audit = state.get("current_claim_audit")
    if prior_verdict is not None and getattr(prior_verdict, "fulfilled", False) \
            and audit is not None:
        hard = audit.hard()
        if hard:
            return [c.statement for c in hard], [c.evidence for c in hard]
    return None, None


# ── researcher ─────────────────────────────────────────────────────────


@timed_node("researcher")
def researcher(state: ResearcherState, runtime: Runtime[PlanContext]) -> ResearcherState:
    """Write the markdown report for the current sub-task. Bumps `attempts`,
    persists to `reports/step_{N}.md`, returns `current_report` + `report_path`.
    """
    ctx = runtime.context
    step = state["step"]
    attempts = state.get("attempts", 0) + 1

    last_report = None
    last_step_unmet = None
    last_step_feedback = None
    last_unsupported = last_suggestions = None
    if attempts > 1:
        last_report = state.get("current_report")
        prior_verdict = state.get("current_step_verdict")
        if prior_verdict is not None:
            last_step_unmet = prior_verdict.unmet_requirements
            last_step_feedback = prior_verdict.feedback
        last_unsupported, last_suggestions = claim_retry_feedback(state)

    retry_block = render_retry_context(
        attempts=attempts,
        max_attempts=ctx.max_n_attempts,
        last_report=last_report,
        last_step_unmet=last_step_unmet,
        last_step_feedback=last_step_feedback,
        last_unsupported_claims=last_unsupported,
        last_claim_suggestions=last_suggestions,
    )

    system = researcher_instructions(
        ctx,
        step,
        retry_block,
        previous_steps_execution_summary=state.get("previous_steps_execution_summary") or "",
    )
    user = (
        "Write the markdown report addressing the current sub-task and every "
        "bullet-point requirement. Output only the report body — no preamble."
    )

    # Multimodal grounding: attach the generated plots so the report is written
    # against the actual figures, not inferred from code/stdout alone.
    user_content = user
    if getattr(ctx, "vlm_enabled", False):
        images = collect_images(state.get("work_dir"), getattr(ctx, "vlm_max_images", 8))
        if images:
            user_content = with_images(
                user + " The figures produced by the analysis are attached below; "
                "read quantitative trends directly from them and make sure your "
                "report is consistent with what the plots show.",
                images,
            )

    msg = _invoke_or_end("researcher", lambda: _proposer(runtime.context).invoke(
        [SystemMessage(system), HumanMessage(content=user_content)],
        config={"tags": ["researcher"]},
    ), label=f"researcher step {state.get('step_number', 1)} attempt {attempts}")
    report = flatten_content(msg.content).strip()

    n = state.get("step_number", 1)
    report_path = ""
    reports = _reports_dir(state)
    if reports is not None:
        out = reports / f"step_{n}.md"
        out.write_text(report)
        report_path = str(out)

    return {
        "attempts": attempts,
        "current_report": report,
        "report_path": report_path,
    }


# ── step_evaluator ─────────────────────────────────────────────────────


@timed_node("researcher_step_evaluator")
def step_evaluator(
    state: ResearcherState, runtime: Runtime[PlanContext]
) -> ResearcherState:
    """Did the report ACHIEVE the sub-task's goal? One structured-output call
    → StepVerdict, judging the report against the run's evidence (prior steps'
    captured output + the workspace file manifest), so a report that covers
    every bullet point with invented numbers fails the gate."""
    ctx = runtime.context
    step = state["step"]
    report = state.get("current_report", "")
    prior_feedback = state.get("step_feedback_history", [])
    n = state.get("step_number", 1)
    attempt = state.get("attempts", 0)

    evidence = build_evidence(state, ctx, legacy_chars=_EVAL_SOURCE_CHARS)
    if evidence.on:
        # The ledger is the audit trail of what the graders were told about
        # presence, per attempt: a later reader can see exactly which numbers
        # the index found, where, and how much of the sources it scanned.
        _persist_json(state.get("work_dir"), f"step_{n}_quantity_ledger_{attempt}.json", {
            "attempt": attempt, "coverage": evidence.index.coverage.to_dict(),
            "quantities": [q.to_dict() for q in evidence.groundings]})

    system = step_evaluator_instructions(
        ctx, step, current_report=report, step_feedback_history=prior_feedback,
        previous_steps_execution_summary=state.get("previous_steps_execution_summary") or "",
        workspace_manifest=_workspace_manifest(state.get("work_dir")),
        read_sources=evidence.stubs,
        quantity_ledger=evidence.ledger,
        grounded=evidence.on,
    )
    user = (
        "Judge whether the researcher's report fulfills the step goal and emit "
        "a verdict. Cover these fields:\n\n" + schema_field_brief(StepVerdict)
    )
    structured = _critic(runtime.context).with_structured_output(StepVerdict)
    verdict: StepVerdict = structured.invoke(
        [SystemMessage(system), HumanMessage(user)],
        config={"tags": ["researcher_step_evaluator"]},
    )

    # A goal-miss that is only a grounding doubt over numbers the index finds
    # in this run's own outputs is not a goal-miss: flip it, hand the spans to
    # the claim auditor, and do NOT record it in the feedback history — the
    # history is what makes the evaluator "stay consistent" on later attempts,
    # and a false fabrication call recorded there is how the livelock locked.
    flipped, hints, flip_records = grounding_flip(verdict, evidence, ctx)
    if flipped:
        print(f"[step_evaluator] step {n} attempt {attempt}: goal-miss was a "
              f"grounding doubt over {len(hints)} span(s) the index finds → fulfilled",
              file=sys.stderr, flush=True)
    if flip_records:
        _persist_json(state.get("work_dir"), f"step_{n}_evaluator_grounding_{attempt}.json",
                      {"attempt": attempt, "flipped": flipped, "spans": flip_records})

    # On a goal-miss, record this attempt's feedback for the next pass.
    feedback_history = list(prior_feedback)
    if not verdict.fulfilled:
        bits = []
        if verdict.unmet_requirements:
            bits.append("unmet — " + "; ".join(verdict.unmet_requirements))
        if verdict.feedback:
            bits.append("feedback — " + verdict.feedback)
        feedback_history.append(
            f"attempt {attempt}: " + (" | ".join(bits) if bits else "goal not met")
        )

    # Researcher-prefixed name so a mixed plan can't collide with an engineer
    # step verdict at the same step number.
    raw = state.get("work_dir")
    if raw:
        logs = Path(raw).expanduser() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"step_{n}_researcher_verdict.json").write_text(
            verdict.model_dump_json(indent=2)
        )

    # Goal-miss: demote the canonical report to a failure-variant for the
    # audit trail.
    if not verdict.fulfilled:
        reports = _reports_dir(state)
        if reports is not None:
            canonical = reports / f"step_{n}.md"
            if canonical.exists():
                canonical.rename(reports / f"step_{n}_failure_{attempt}.md")

    out: ResearcherState = {
        "current_step_verdict": verdict,
        "step_feedback_history": feedback_history,
    }
    # A goal-miss whose verdict or report says the work needs code: hand the step to the
    # engineer (deep_research.run_step) rather than rewriting the report again.
    if not verdict.fulfilled and state.get("engineer_reassignment_allowed"):
        from cmbagent_lg.planning.agent_check import needs_code_signal, reassignment_enabled

        signal = needs_code_signal(verdict, report, state.get("step")) if reassignment_enabled(ctx) else None
        if signal:
            out["needs_engineer"] = {**signal, "attempt": attempt}
            print(f"[researcher] step {n} attempt {attempt}: not fulfilled and the {signal['source']} says the "
                  f"work needs code ({signal['matched']!r}) → ending the researcher loop for the engineer",
                  file=sys.stderr, flush=True)
    if evidence.on:
        out["claim_hints"] = hints
        out["source_index_key"] = evidence.index.key
    return out


# ── router ──────────────────────────────────────────────────────────────


def writer_node_name(ctx: PlanContext) -> str:
    """Which report-writing node this run uses — `ctx.researcher_mode`,
    defaulting to the grounded ReAct writer. Anything unrecognised means the
    plain node, so a bad value degrades rather than raising."""
    return ("react_researcher"
            if getattr(ctx, "researcher_mode", "react") == "react"
            else "researcher")


def route_after_step_evaluator(
    state: ResearcherState, runtime: Runtime[PlanContext]
) -> str:
    """Goal gate: fulfilled → the claim_auditor (if enabled) else END; not
    fulfilled & attempts left → the run's report writer; not fulfilled &
    exhausted → END. Only the FULFILLED branch reaches the auditor, so the
    auditor stays off the goal router and can never flip a verdict."""
    ctx = runtime.context
    if state["current_step_verdict"].fulfilled:
        return "claim_auditor" if getattr(ctx, "claim_audit_enabled", True) else END
    if state.get("needs_engineer"):
        return END          # deep_research.run_step gives the step to the engineer
    if state.get("attempts", 0) >= ctx.max_n_attempts:
        return END
    return writer_node_name(ctx)


__all__ = ["researcher", "step_evaluator", "route_after_step_evaluator",
           "writer_node_name", "build_evidence", "grounding_flip", "Evidence"]
