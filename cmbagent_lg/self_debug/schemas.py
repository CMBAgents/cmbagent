"""Pydantic schemas for the self_debug loop.

Two distinct verdicts, mirroring cmbagent's split:
- `ExecutionVerdict` — did the code RUN cleanly? (cmbagent's executor_response_evaluator)
- `StepVerdict` — did the run ACHIEVE the sub-task's goal? (cmbagent's controller)
"""

import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class EngineerResponse(BaseModel):
    """Structured output of the engineer + format_engineer pair."""

    python_code: str = Field(
        description="Single self-contained Python script, ready to be executed by "
        "`python <file.py>` in the host venv. No CLI arguments, wrapped in "
        "`if __name__ == '__main__':`."
    )
    code_explanation: str = Field(
        description="One concise paragraph explaining what the script does."
    )
    modification_summary: Optional[str] = Field(
        default=None,
        description="If this is a retry, summarize what changed vs the previous "
        "attempt and why. Omit on the first attempt.",
    )

    def format(self) -> str:
        out = "**Code Explanation:**\n" + self.code_explanation + "\n"
        if self.modification_summary:
            out += "\n**Modifications:**\n" + self.modification_summary + "\n"
        out += "\n**Python Code:**\n```python\n" + self.python_code + "\n```\n"
        return out


class ExecutionVerdict(BaseModel):
    """Structured output of the execution_evaluator node — judges only whether
    the last subprocess RAN cleanly, not whether the step goal was met."""

    status: Literal["success", "failure"] = Field(
        description="`success` if the script ran cleanly (no exception, no error — "
        "including caught-and-printed errors); `failure` otherwise."
    )
    failure_kind: Optional[Literal["missing_module", "renamed_api", "other"]] = Field(
        default=None,
        description="On failure, classify the root cause: `missing_module` (a "
        "package is not installed), `renamed_api` (a function/attribute was "
        "renamed/moved/removed from an installed package), or `other` (logic "
        "bug, bad value, timeout, …). Null on success. Drives escalation.",
    )
    error_summary: Optional[str] = Field(
        default=None,
        description="On failure: one-paragraph description of what went wrong, with the "
        "key traceback line if useful. Null on success.",
    )
    fix_suggestion: Optional[str] = Field(
        default=None,
        description="On failure: concrete, actionable guidance for the engineer's "
        "next attempt. Null on success.",
    )

    # No `structured_answer_problem`: a failure with an empty `error_summary` is usable as it is, since
    # execution_evaluator fills the summary from the last stderr line; rejecting it cost a retry and, when the retry
    # was blank too, replaced the model's `failure_kind` (e.g. missing_module) with the fallback's "other".

    def format(self) -> str:
        out = f"**Status:** {self.status}\n"
        if self.error_summary:
            out += f"\n**Error summary:**\n{self.error_summary}\n"
        if self.fix_suggestion:
            out += f"\n**Fix suggestion:**\n{self.fix_suggestion}\n"
        return out


class StepVerdict(BaseModel):
    """Structured output of the step_evaluator node — judges whether the run
    ACHIEVED the sub-task's goal (the code is already known to have run
    cleanly). Mirrors cmbagent's controller verdict."""

    fulfilled: bool = Field(
        description="True only if every part of the sub-task AND every "
        "bullet-point requirement is satisfied by this run."
    )
    unmet_requirements: List[str] = Field(
        default_factory=list,
        description="When not fulfilled: the specific sub-task parts / bullet "
        "points that were not satisfied. Empty when fulfilled.",
    )
    feedback: Optional[str] = Field(
        default=None,
        description="When not fulfilled: concrete, actionable guidance for the "
        "engineer's next attempt. Null when fulfilled.",
    )
    # Researcher steps only; the engineer path never sets it. A grounding doubt
    # goes HERE, not into `fulfilled`: presence/absence of a number is decided
    # deterministically afterwards, so the evaluator's suspicion is a hint for
    # the claim auditor rather than a verdict.
    suspected_fabrications: List[str] = Field(
        default_factory=list,
        description="Researcher reports only: spans of the report (quoted "
        "verbatim) whose specific values you could not find in the evidence "
        "shown. List them here rather than failing the step for them — "
        "presence in the run's full sources is checked deterministically "
        "afterwards. Empty for engineer steps and when nothing is suspect.",
    )
    # Engineer steps only. The escape hatch the verdict previously lacked: with no way
    # to say "this requirement cannot be satisfied with the installed environment or
    # the available data", the evaluator could only fail the step again, and the
    # engineer could only resubmit. On the breast-cancer fleet eight projects spent
    # their whole 8-attempt budget that way (30% of all attempt wall-clock) on
    # requirements such as a Fine-Gray regression that no installed library implements.
    # Adjudicated by `infeasible_substitutions` below, NOT by the model: a declaration
    # only lands if it covers EVERY unmet item.
    infeasible_requirements: List[str] = Field(
        default_factory=list,
        description="Engineer steps only; leave EMPTY for a researcher report. The "
        "unmet requirements that CANNOT be satisfied with the installed environment "
        "or the data this run actually has — a method no installed library provides, "
        "or a variable the dataset does not contain. Repeat each one VERBATIM from "
        "`unmet_requirements` (both lists must name it). Use this instead of failing "
        "the step again for it. Only for a genuine impossibility — never for work the "
        "engineer simply has not done yet, and never to excuse a wrong result.",
    )
    infeasible_justification: Optional[str] = Field(
        default=None,
        description="Engineer steps only. One or two sentences naming WHAT is missing "
        "— the absent library/API or the absent data column — and what the run "
        "delivered instead. Null when `infeasible_requirements` is empty.",
    )

    def structured_answer_problem(self) -> Optional[str]:
        """Why a model's verdict cannot be used, or None. A step that is not fulfilled must give a reason: an unmet
        requirement, a suspected fabrication (researcher), an infeasible requirement, or feedback. Qwen with thinking
        off answered `{"fulfilled": false}` and nothing else (probe of 23 Nov 2026), which would fail the attempt with
        no feedback for the engineer. Checked on answers from OpenAI-compatible endpoints only (`llms._check_answer`),
        where it earns the one structured retry (a retry answer that fails it is used as it is, and marked);
        Gemini answers and verdicts built in code are unchanged."""
        if self.fulfilled:
            return None
        reasons = [*self.unmet_requirements, *self.suspected_fabrications, *self.infeasible_requirements,
                   self.feedback or ""]
        if not any((r or "").strip() for r in reasons):
            return ("fulfilled is false but no reason is given (unmet_requirements, suspected_fabrications, "
                    "infeasible_requirements and feedback are all empty)")
        return None

    def format(self) -> str:
        out = f"**Step fulfilled:** {self.fulfilled}\n"
        if self.unmet_requirements:
            out += "\n**Unmet requirements:**\n"
            for r in self.unmet_requirements:
                out += f"- {r}\n"
        if self.infeasible_requirements:
            out += "\n**Infeasible in this environment:**\n"
            for r in self.infeasible_requirements:
                out += f"- {r}\n"
        if self.infeasible_justification:
            out += f"\n**Why infeasible:**\n{self.infeasible_justification}\n"
        if self.feedback:
            out += f"\n**Feedback:**\n{self.feedback}\n"
        return out


# ── infeasible-requirement adjudication (engineer path) ──────────────────────
#
# Deliberately deterministic and outside the model's reach: the verdict only says WHICH
# requirements it believes impossible, and these two functions decide whether that makes
# the step complete. Both are duck-typed so a verdict loaded from an old transcript (no
# such fields) and a researcher verdict read through the same code path answer "no".

# Minimum length, in characters, of the shorter string before CONTAINMENT counts as "the
# same requirement". The evaluator is told to repeat the unmet item verbatim, so equality
# is the contract and containment only absorbs a near-verbatim echo; without the floor a
# two-word declaration ("the data") would cover any requirement containing it.
_MIN_INFEASIBLE_OVERLAP = 20


def _norm_requirement(s: str) -> str:
    """Comparison form: collapsed whitespace, no case, no edge punctuation."""
    return re.sub(r"\s+", " ", s or "").strip().strip(".;,:-\u2014 ").casefold()


def _same_requirement(unmet: str, declared: str) -> bool:
    """Do these two strings name the same requirement? Equal after normalisation, or
    one contains the other and the shorter is at least `_MIN_INFEASIBLE_OVERLAP` long."""
    a, b = _norm_requirement(unmet), _norm_requirement(declared)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= _MIN_INFEASIBLE_OVERLAP and short in long


def _evidence_refuses(run_state) -> bool:
    """Did the engine's own audit of this claim REFUTE it?

    The audit (`self_debug/infeasibility_check.audit_declaration`) is computed once in
    `step_evaluator`, where the step, the manifest and the installed environment are all
    in hand, and left on state as `infeasibility_check`. This function only reads it, so
    the schema layer stays free of imports and every caller gets the same answer.

    FAILS CLOSED: when a caller supplies `run_state` but it carries no audit, the
    declaration is refused. An unaudited claim is exactly the evaluator's bare word,
    which is what this channel must not run on; refusing degrades to the old
    behaviour (the step fails), never to a silent pass.
    """
    if run_state is None:
        return False                      # no-evidence form: textual rules only
    audit = run_state.get("infeasibility_check") if isinstance(run_state, dict) else None
    if not isinstance(audit, dict):
        return True
    return audit.get("status") == "refuted"


def infeasible_substitutions(verdict, step=None, run_state=None) -> List[str]:
    """What this verdict declared infeasible — but ONLY when the declaration covers
    EVERY requirement still standing as unmet; otherwise `[]`.

    All-or-nothing on purpose: a partially-infeasible step must not silently pass, so
    anything unmet and NOT declared infeasible still fails the step.

    `infeasible_requirements` is the claim; `unmet_requirements` need not repeat it. The
    prompt asks for the item in both lists, because keeping it unmet is honest — it
    really was not satisfied — but an evaluator that MOVES the item instead of listing
    it twice is making the same statement, and reading that as "nothing was declared"
    would silently drop the whole channel. So an empty `unmet_requirements` beside a
    non-empty declaration is accepted, and anything that IS listed unmet must be
    covered.

    This function decides only the TEXTUAL question — did the evaluator's two lists line
    up. Whether the EVIDENCE supports the claim is decided by
    `infeasibility_check.audit_declaration` and consulted here through `run_state`
    (`_evidence_refuses`), which also makes an unaudited claim fail closed. `step` is
    accepted for symmetry with that audit and for callers that pass both. Omitting
    `run_state` gives the no-evidence form, which applies the textual rule alone and is
    what the unit tests use.

    `[]` for a fulfilled verdict (the step is complete on its own terms), for an empty
    declaration, and for any object without the fields — an old transcript included.
    """
    declared = [d for d in (getattr(verdict, "infeasible_requirements", None) or []) if (d or "").strip()]
    if not declared or getattr(verdict, "fulfilled", False):
        return []
    if _evidence_refuses(run_state):
        return []
    unmet = [u for u in (getattr(verdict, "unmet_requirements", None) or []) if (u or "").strip()]
    if not all(any(_same_requirement(u, d) for d in declared) for u in unmet):
        return []
    # The unmet wording is the requirement as the sub-task phrased it, so prefer it;
    # fall back to the declaration when the evaluator moved the item rather than
    # repeating it.
    return list(unmet or declared)


def step_goal_met(verdict, step=None, run_state=None) -> bool:
    """The goal gate's question: is this step done? True when the verdict is
    `fulfilled`, or when every unmet requirement was declared infeasible AND the
    engine's own audit of that claim did not refute it (see `infeasible_substitutions`;
    pass `run_state` so the audit is consulted). Any verdict without the new fields
    behaves exactly as before."""
    if verdict is None:
        return False
    if getattr(verdict, "fulfilled", False):
        return True
    return bool(infeasible_substitutions(verdict, step, run_state))
class InputSourcedClaim(BaseModel):
    """Forced-choice classification of a report claim whose numbers were located ONLY
    in a file the run was GIVEN (see `researcher/grounding.py`'s input-sourced envelope).

    The only model judgement in the engine that can weaken a deterministic finding, so
    it is deliberately narrow: two choices, and a span the model must quote verbatim out
    of the claim — a classification whose span is not really in the text is discarded.
    """

    kind: Literal["description_of_provided_data", "asserted_result"] = Field(
        description="`description_of_provided_data` — the sentence RESTATES something "
        "the run was given (the cohort's composition, the dataset's size, a column's "
        "range), and the numbers belong to that description. "
        "`asserted_result` — the sentence presents the numbers as a FINDING of this "
        "analysis (something explained, established, measured, compared or concluded), "
        "which an input file cannot support. Choose `asserted_result` whenever you are "
        "unsure, and whenever the sentence does both."
    )
    span: str = Field(
        description="The span of the claim your choice rests on, quoted VERBATIM and "
        "character-for-character from the statement (never a paraphrase, never "
        "re-punctuated). It is checked against the statement and your answer is "
        "discarded if it is not found."
    )
    justification: str = Field(
        description="One sentence saying why that span is a description of provided "
        "data, or why it is an asserted result."
    )


class DataLoaderFix(BaseModel):
    """Structured output of the `data_loader_fixer` node (R-05). Given a
    deterministic inspection report of the real input file (magic bytes,
    sniffed delimiter, HDF5 key tree, npy dtype, …), the model synthesises the
    correct loader so the NEXT engineer attempt reads the file right first try.

    `format="missing"` is reserved for the deterministic path (an absent
    required input) — the model is asked to REFUSE to fabricate, never to
    invent a loader for a file that is not there.
    """

    format: Literal["recoverable", "missing", "unknown"] = Field(
        description="`recoverable` — the file is present and a correct loader "
        "can be written; `missing` — a required input file is genuinely absent "
        "(do NOT fabricate data); `unknown` — cannot determine a fix.",
    )
    loader_snippet: str = Field(
        default="",
        description="When recoverable: a minimal, correct Python snippet that "
        "loads the real file into clean numeric arrays/columns (right delimiter, "
        "decimal, comment, encoding, compression, HDF5 dataset path, "
        "allow_pickle, etc.). Empty when missing/unknown.",
    )
    column_guidance: str = Field(
        default="",
        description="When recoverable: how to select the TASK's target column "
        "BY NAME (never by positional dtype) and assert it is numeric+finite. "
        "Empty otherwise.",
    )
    notes: str = Field(
        default="",
        description="One or two sentences: the diagnosed real format and, when "
        "missing, why the input cannot be loaded (so the engineer exits non-zero "
        "instead of synthesising data).",
    )

    def format_guidance(self) -> str:
        out = f"**Diagnosed input format:** {self.format}\n"
        if self.notes:
            out += f"\n{self.notes}\n"
        if self.loader_snippet:
            out += (
                "\n**Use THIS loader (it was derived from the real file bytes — "
                "trust it over your own assumptions):**\n```python\n"
                + self.loader_snippet
                + "\n```\n"
            )
        if self.column_guidance:
            out += "\n**Target column:** " + self.column_guidance + "\n"
        return out


class IntentVerdict(BaseModel):
    """Structured output of the pre-flight INTENT GATE (R-05 strategy C).

    Asked exactly once per step, and only when `data/` was found EMPTY: does
    this sub-task require input data that was supposed to be PROVIDED to the
    run, or is CREATING the data the point of the step? The distinction cannot
    be made from the empty directory alone — an empty `data/` is a broken setup
    for the first kind of task and completely expected for the second.
    """

    needs_provided_input: bool = Field(
        description="True if the sub-task requires reading data that was "
        "supposed to be PROVIDED to the run (e.g. 'load the signal in data/', "
        "'fit the provided spectrum', 'apply data/calib.csv'). False if the "
        "script is meant to CREATE the data it works on (simulate, generate, "
        "sample, compute-and-write), or if it needs no input file at all."
    )
    reason: str = Field(
        description="One short sentence quoting the wording that decided it.",
    )


class ImageReview(BaseModel):
    """Structured output of the `image_reviewer` node — a visual review of the
    plots a step produced (mirrors cmbagent's `image_reviewer` agent). Drives a
    bounded revise-the-plot loop back to the engineer."""

    needs_revision: bool = Field(
        description="True ONLY if a figure has a concrete, fixable defect "
        "(unreadable/overlapping/cut-off text, missing axis labels or units, "
        "missing legend when multiple series, wrong/!misleading scale, or it "
        "doesn't show what the sub-task asked for). False for purely stylistic "
        "preferences or when the figures are acceptable."
    )
    issues: List[str] = Field(
        default_factory=list,
        description="When needs_revision: the specific problems, one concrete "
        "item each (e.g. 'y-axis label is cut off', not 'labels have issues'). "
        "Empty otherwise.",
    )
    suggestions: List[str] = Field(
        default_factory=list,
        description="When needs_revision: concrete, actionable plotting fixes "
        "for the engineer (e.g. 'add plt.tight_layout()', 'set xlabel with "
        "units'). Empty otherwise.",
    )
    summary: str = Field(
        default="",
        description="One- or two-sentence summary of the review.",
    )

    def format(self) -> str:
        out = f"**Needs revision:** {self.needs_revision}\n"
        if self.issues:
            out += "\n**Issues:**\n" + "".join(f"- {i}\n" for i in self.issues)
        if self.suggestions:
            out += "\n**Suggestions:**\n" + "".join(f"- {s}\n" for s in self.suggestions)
        if self.summary:
            out += f"\n**Summary:** {self.summary}\n"
        return out


class ClaimCheck(BaseModel):
    """One assertion in a researcher report, labelled against the run's evidence
    (R-13). Compound sentences are split so a supported clause cannot shield a
    fabricated one; `statement` quotes the report VERBATIM so it can be located."""

    statement: str = Field(
        description="The report's assertion, quoted VERBATIM (a copied span from "
        "the report, never a paraphrase) so it can be found in the text."
    )
    kind: Literal[
        "quantitative", "figure_feature", "test_outcome",
        "comparison", "derived", "null_result", "other",
    ] = Field(
        description="What kind of result-claim this is: a specific value/"
        "uncertainty (`quantitative`), a stated figure feature — peak/trend/level/"
        "crossing/absence (`figure_feature`), a test statistic/outcome "
        "(`test_outcome`), a comparison — 'A larger than B' (`comparison`), a "
        "value computed from others — ratio/difference/normalised (`derived`), a "
        "definite null — 'no correlation' (`null_result`), or `other`."
    )
    verdict: Literal[
        "supported", "unsupported_hard", "unsupported_weak", "uncheckable",
    ] = Field(
        description="`supported` — a clear signal in the evidence backs it (a "
        "printed value, a read-artifact excerpt, a figure reading, a named data "
        "file, or arithmetic you reproduced from supported inputs). "
        "`unsupported_hard` — a SPECIFIC result-claim with NO matching signal "
        "anywhere, or one that CONTRADICTS a signal (wrong trend, claimed "
        "agreement no step produced), or precision beyond every source, or a code "
        "INPUT restated as a measurement: a fabrication, must be fixed. "
        "`unsupported_weak` — plausibly true but not confirmable from the "
        "available evidence (a value that may be present but was truncated away, a "
        "derivation whose inputs you could not locate, a missing citation, a "
        "vision-off qualitative impression): ADVISORY only. `uncheckable` — not a "
        "result-claim about this session (method/motivation prose, a restatement "
        "of the task, a definition, a hedge, or an honest abstention that names an "
        "artifact)."
    )
    evidence: str = Field(
        description="For `supported`: where the signal is (the printed value, the "
        "figure reading, the named file, or the arithmetic you reproduced). For "
        "unsupported: what is missing and what the writer should do — point to the "
        "printed value / figure reading / named data file it came from, soften it "
        "to what the signal supports, or remove it; never invent a source."
    )
    # Set by the deterministic grounding pass (never by the LLM): the auditor's
    # `unsupported_hard` was demoted because the claim's numbers exist in the
    # run's own outputs. Optional so older audit records still load.
    grounding_override: Optional[bool] = Field(
        default=None,
        description="Leave unset. Filled in afterwards by the deterministic "
        "grounding pass when it demotes a verdict.",
    )


class ClaimAudit(BaseModel):
    """Structured output of the `claim_auditor` node (R-13) — a statement-by-
    statement grounding audit of a researcher report that step_evaluator has
    ALREADY accepted on goal. Only `unsupported_hard` claims drive a revision or
    fail the step; `unsupported_weak` is advisory and `uncheckable` is ignored."""

    claims: List[ClaimCheck] = Field(
        default_factory=list,
        description="One entry per atomic result-claim in the report. Split "
        "compound sentences so each checkable assertion is judged on its own.",
    )
    summary: str = Field(
        default="",
        description="One or two sentences summarising the audit outcome.",
    )

    def hard(self) -> List[ClaimCheck]:
        return [c for c in self.claims if c.verdict == "unsupported_hard"]

    def weak(self) -> List[ClaimCheck]:
        return [c for c in self.claims if c.verdict == "unsupported_weak"]

    @property
    def needs_revision(self) -> bool:
        return bool(self.hard())

    def format(self) -> str:
        hard, weak = self.hard(), self.weak()
        out = f"**Unsupported (must fix): {len(hard)}  ·  advisory: {len(weak)}**\n"
        if hard:
            out += "\n**Unsupported claims:**\n" + "".join(
                f'- "{c.statement}" — {c.evidence}\n' for c in hard)
        if weak:
            out += "\n**Advisory (not blocking):**\n" + "".join(
                f'- "{c.statement}" — {c.evidence}\n' for c in weak)
        if self.summary:
            out += f"\n**Summary:** {self.summary}\n"
        return out
