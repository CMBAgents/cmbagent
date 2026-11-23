"""Prompt loaders for the self_debug loop. Same shape as planning/prompts.py."""

from typing import List

from cmbagent_lg.context import PlanContext, effective_code_execution_timeout
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.prompt_utils import SafeDict, load_yaml


_TEMPLATES = "cmbagent_lg.self_debug.templates"
ENGINEER_YAML = load_yaml(_TEMPLATES, "engineer.yaml")
EVALUATOR_YAML = load_yaml(_TEMPLATES, "evaluator.yaml")
STEP_EVALUATOR_YAML = load_yaml(_TEMPLATES, "step_evaluator.yaml")
ESCALATION_YAML = load_yaml(_TEMPLATES, "escalation.yaml")


_TRUNCATE_HEAD = 1500
_TRUNCATE_TAIL = 500


def _head_tail(s: str, head: int = _TRUNCATE_HEAD, tail: int = _TRUNCATE_TAIL) -> str:
    """Truncate a long string to head + marker + tail (cmbagent's HeadTailTokenTruncate
    idea). The marker says HOW MUCH was cut and WHERE: an unmarked cut is the bug
    that let graders rule a value "absent" from a window that never held it."""
    if not s or len(s) <= head + tail + 4:
        return s
    from cmbagent_lg.researcher.grounding import truncation_marker
    return s[:head] + "\n" + truncation_marker(len(s) - head - tail, head, len(s) - tail, len(s)) + "\n" + s[-tail:]


# A run that printed nothing must say so: a blank line reads to an evaluator as
# "no problems here" rather than "no evidence at all".
NO_STDOUT_MARKER = "(the script produced NO stdout — nothing to verify)"


def _render_stdout(s: str | None, head: int = _TRUNCATE_HEAD, tail: int = _TRUNCATE_TAIL) -> str:
    """`_head_tail`, but empty/whitespace-only output renders as an explicit marker."""
    if not s or not s.strip():
        return NO_STDOUT_MARKER
    return _head_tail(s, head, tail)


def _render_code(code: str | None, head: int = 8000, tail: int = 3000) -> str:
    """The executed script, capped. The evaluator reads it to confirm which
    parameters/methods were actually used, and to spot data GENERATED where it should
    have been LOADED."""
    if not code or not code.strip():
        return "(no code available)"
    return _head_tail(code, head, tail)


# A timeout reported like any other crash gets the same algorithm resubmitted, so this
# names the failure MODE and the concrete escape routes.
_TIMEOUT_STEER = """**This is a PERFORMANCE failure, not a logic bug — do NOT resubmit the same
algorithm with small edits, and do NOT try to raise the time limit (you cannot;
it is a hard cap). Pick a method that fits the budget:**
  - vectorize the hot loops (numpy/pandas whole-array ops — no per-row Python);
  - subsample, chunk, or stream the data instead of loading/scanning all of it;
  - replace pairwise/nested-loop scans with a spatial index or a hash/merge join;
  - cut the iteration count — fewer epochs, grid points, bootstrap replicates,
    MCMC samples — or loosen a convergence tolerance;
  - cache/precompute work repeated inside a loop, and drop anything the
    sub-task does not actually require;
  - choose a cheaper estimator or a closed-form/approximate solution.
Say in **Modifications:** which of these you applied and why the new version
fits the budget."""


# Header the step/execution evaluators see above the ENGINEER's own operator text.
# Labelled, never merged into the evaluator's own directions: the evaluator has to know
# what the engineer was told (it cannot otherwise judge a substitution the operator
# asked for), but it must not read the engineer's how-to-code guidance as instructions
# to itself. See `PlanContext.shared_append_instructions` for the full contract and the
# metabric measurement that motivates it.
_ENGINEER_TEXT_HEADER = (
    "INSTRUCTIONS THE ENGINEER WAS GIVEN FOR THIS RUN (shown so you judge the script "
    "against the same facts and the same definition of a finished step that the "
    "engineer was working to — they are the engineer's directions, not yours):"
)


def _join_blocks(*parts: str | None) -> str:
    """Non-empty blocks joined by a blank line; "" when every part is empty, so a
    prompt with no operator text is byte-for-byte what it was before."""
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def engineer_append_block(ctx: PlanContext) -> str:
    """Operator text for the ENGINEER's prompt: the shared block then its own.
    Both are addressed to it, so neither needs a label."""
    return _join_blocks(
        getattr(ctx, "shared_append_instructions", ""),
        getattr(ctx, "engineer_append_instructions", ""),
    )


def evaluator_append_block(ctx: PlanContext) -> str:
    """Operator text for an EVALUATOR's prompt: the shared block, then the engineer's
    own text under `_ENGINEER_TEXT_HEADER`, then the evaluator's own."""
    engineer_text = (getattr(ctx, "engineer_append_instructions", "") or "").strip()
    return _join_blocks(
        getattr(ctx, "shared_append_instructions", ""),
        f"{_ENGINEER_TEXT_HEADER}\n{engineer_text}" if engineer_text else "",
        getattr(ctx, "evaluator_append_instructions", ""),
    )


def render_retry_context(
    attempts: int,
    max_attempts: int,
    last_code: str | None,
    last_stderr: str | None,
    last_stdout: str | None,
    last_returncode: int | None,
    last_timed_out: bool | None,
    last_fix_suggestion: str | None,
    error_history: List[str],
    last_step_unmet: List[str] | None = None,
    last_step_feedback: str | None = None,
    last_step_infeasible: List[str] | None = None,
    last_image_issues: List[str] | None = None,
    last_image_suggestions: List[str] | None = None,
    data_fixer_guidance: str | None = None,
    last_elapsed_s: float | None = None,
    timeout_s: int | None = None,
    api_introspect_report: str | None = None,
    api_preflight_report: str | None = None,
    bypass_after_fault_report: str | None = None,
) -> str:
    """Block injected into the engineer prompt on retries. Empty on first attempt.

    Three modes: a **code-error** retry shows the error trail (selected when all the
    `last_step_*` / `last_image_*` args are None); a **success-path** retry shows the
    figure issues, unmet requirements and reviewer feedback; and a
    **bypass-after-fault** retry shows stdout plus the nudge and nothing else, since the
    router sent it back before either verdict was produced.
    """
    if attempts <= 1:
        return ""

    remaining = max_attempts - attempts + 1
    lines = [
        f"**Attempt {attempts} of {max_attempts} ({remaining} remaining).**",
        "",
    ]

    if attempts >= max_attempts:
        lines.append("**LAST CHANCE — if this attempt fails, the loop terminates.**")
        lines.append("")

    # Loader guidance from the data_loader_fixer's inspection of the real file. Placed
    # FIRST so it dominates the retry — it comes from the actual bytes.
    if data_fixer_guidance:
        lines += [
            "**DATA-LOADER GUIDANCE (from direct inspection of the real input "
            "file — trust this over your own assumptions about the format):**",
            "",
            data_fixer_guidance,
            "",
        ]

    if last_code:
        lines += [
            "**The previous attempt's code was:**",
            "```python",
            _head_tail(last_code),
            "```",
            "",
        ]

    # The code above was never RUN (the API pre-flight proved a name absent), so any
    # stdout/stderr in state belongs to an EARLIER attempt and rendering it here would
    # attribute an old traceback to code that never executed. The report replaces the
    # whole outcome block.
    if api_preflight_report:
        lines += [api_preflight_report, ""]
        return "\n".join(lines)

    # A success that dropped a genuinely-faulted library. Own mode, checked before the
    # ordinary success/code-error split — see the docstring above.
    if bypass_after_fault_report:
        lines += [
            "**Outcome:** the code ran cleanly and produced output.",
            "",
            "**stdout:**", "```", _render_stdout(last_stdout), "```", "",
            bypass_after_fault_report,
            "",
        ]
        return "\n".join(lines)

    success_retry = bool(
        last_step_unmet or last_step_feedback
        or last_image_issues or last_image_suggestions
    )
    if success_retry:
        lines += [
            "**Outcome:** the code ran cleanly, but the output needs revision "
            "(figure review and/or step goal).",
            "",
        ]
        # Unconditional: "it printed nothing" is itself the feedback the engineer needs,
        # and is often WHY the step was rejected.
        lines += ["**stdout:**", "```", _render_stdout(last_stdout), "```", ""]
        # The script exited 0 but CAUGHT an API error and printed it to the stdout above,
        # so place the facts under the output they explain.
        if api_introspect_report:
            lines += [api_introspect_report, ""]
        if last_image_issues or last_image_suggestions:
            lines.append("**Figure review — the generated plot(s) need revision:**")
            if last_image_issues:
                lines.append("Issues:")
                for i in last_image_issues:
                    lines.append(f"  - {i}")
            if last_image_suggestions:
                lines.append("Suggested fixes:")
                for s in last_image_suggestions:
                    lines.append(f"  - {s}")
            lines.append("")
        if last_step_unmet:
            lines.append("**Unmet requirements:**")
            for r in last_step_unmet:
                lines.append(f"  - {r}")
            lines.append("")
        # A PARTIALLY-infeasible verdict: the evaluator itself judged these impossible
        # in this environment (a fully-infeasible one never comes back here — the goal
        # gate accepts it). Saying so stops the retry being spent re-arguing them,
        # which is the loop `StepVerdict.infeasible_requirements` exists to break.
        if last_step_infeasible:
            lines.append(
                "**The evaluator judged these IMPOSSIBLE in this environment — do "
                "NOT attempt them again and do not argue about them; spend this "
                "attempt on the other unmet requirements above:**"
            )
            for r in last_step_infeasible:
                lines.append(f"  - {r}")
            lines.append("")
        if last_step_feedback:
            lines += ["**Reviewer feedback:**", last_step_feedback, ""]
    else:
        if last_timed_out:
            ran = f"ran for {last_elapsed_s:.0f}s and " if last_elapsed_s else ""
            budget = f"the {timeout_s}s budget" if timeout_s else "the execution-time limit"
            lines += [
                f"**Outcome:** TIMED OUT — the script {ran}exceeded {budget}; "
                f"its whole process group was killed.",
                "",
                _TIMEOUT_STEER,
                "",
            ]
        elif last_returncode is not None:
            lines.append(f"**Outcome:** returncode {last_returncode}")
            lines.append("")
        else:
            lines.append("")
        if last_stderr:
            lines += ["**stderr:**", "```", _head_tail(last_stderr), "```", ""]
        # What the INSTALLED package actually contains, placed directly under the stderr
        # it explains. None unless `api_introspect_retry` is on and the error parsed.
        if api_introspect_report:
            lines += [api_introspect_report, ""]
        # Unconditional — see the success-path branch above.
        lines += ["**stdout:**", "```", _render_stdout(last_stdout), "```", ""]
    # Both retry paths need the history, not just the code-error one. After a CLEAN
    # run that the step evaluator judged unfulfilled, an engineer shown no crash
    # history reverts the very change that escaped the previous crash. That is a
    # 2-cycle, and it burned all eight attempts of Project7 step 7 on 2026-10-10:
    # init='random' ran clean -> judged unfulfilled -> init='svd' -> 41.2 TiB
    # allocation -> repeat, three times over.
    if last_fix_suggestion:
        lines += ["**Evaluator's fix suggestion (from the last attempt that crashed):**",
                  last_fix_suggestion, ""]
    if len(error_history) > 1:
        lines.append("**Cumulative error history (do not repeat fixes that already failed):**")
        for i, err in enumerate(error_history[:-1], start=1):
            lines.append(f"  - attempt {i}: {err}")
        lines.append("")

    return "\n".join(lines)


def render_preflight_block(ctx: PlanContext, preflight_report: str | None) -> str:
    """FILE REPORT block for the engineer's FIRST attempt: the deterministic file
    inspection plus a general hook (facts, not a fault checklist). Empty unless
    `enable_preflight` is on AND a non-empty report is present."""
    if not getattr(ctx, "enable_preflight", False) or not preflight_report:
        return ""
    hook = ENGINEER_YAML.get("preflight_hook", "").strip()
    lines = [
        "**FILE REPORT — deterministic inspection of the provided input "
        "file(s) in data/ (facts from the real bytes, no model involved):**",
        "",
        preflight_report,
        "",
    ]
    if hook:
        lines += [hook, ""]
    return "\n".join(lines)


def render_intent_gate_directive(
    ctx: PlanContext,
    needs_provided_input: bool | None,
    input_dir_empty: bool | None,
) -> str:
    """Hard do-NOT-fabricate directive for the engineer.

    Rendered only when BOTH facts hold: the intent gate classified this sub-task as
    needing PROVIDED input, and `data/` was verified empty before any code ran. Every
    other combination renders "", so a generation task's prompt is untouched."""
    if not getattr(ctx, "enable_intent_gate", True):
        return ""
    if not (needs_provided_input and input_dir_empty):
        return ""
    return ENGINEER_YAML.get("missing_input_directive", "").strip() + "\n"


def engineer_instructions(
    ctx: PlanContext,
    step: Step,
    retry_context: str,
    previous_steps_execution_summary: str = "",
    preflight_report: str | None = None,
    needs_provided_input: bool | None = None,
    input_dir_empty: bool | None = None,
    api_digest: str | None = None,
) -> str:
    # The "Loading a provided INPUT data file" block is gated behind `enable_data_fixer`
    # and renders as "" otherwise, leaving the default prompt unchanged.
    data_loading_guidance = (
        ENGINEER_YAML.get("data_loading_guidance", "")
        if getattr(ctx, "enable_data_fixer", False)
        else ""
    )
    # The FILE REPORT and the do-not-fabricate directive share one placeholder: they are
    # mutually exclusive in practice (a report exists only when there ARE files, the
    # directive only when there are none), so reusing it keeps the YAML template — and
    # every other path's prompt — byte-for-byte unchanged.
    preflight_block = render_preflight_block(ctx, preflight_report) + render_intent_gate_directive(
        ctx, needs_provided_input, input_dir_empty
    )
    # `enable_api_digest` shares the same slot for the same reason: it is "facts
    # established before the engineer starts". None wherever the flag is off.
    if api_digest:
        preflight_block = preflight_block + api_digest + "\n"
    return ENGINEER_YAML["instructions"].format_map(
        SafeDict(
            main_task=ctx.main_task,
            engineer_append_instructions=engineer_append_block(ctx),
            hardware_constraints=ctx.hardware_constraints,
            # Always a real number (never None) — the run is always bounded.
            code_execution_timeout=effective_code_execution_timeout(ctx, step),
            current_sub_task=step.sub_task,
            current_instructions="\n".join(f"- {b}" for b in step.bullet_points),
            retry_context=retry_context,
            previous_steps_execution_summary=previous_steps_execution_summary,
            data_loading_guidance=data_loading_guidance,
            preflight_report=preflight_block,
        )
    )


def evaluator_instructions(
    ctx: PlanContext,
    step: Step,
    executed_code: str,
    stdout: str,
    stderr: str,
    returncode: int,
    timed_out: bool,
) -> str:
    return EVALUATOR_YAML["instructions"].format_map(
        SafeDict(
            evaluator_append_instructions=evaluator_append_block(ctx),
            code_execution_timeout=effective_code_execution_timeout(ctx, step),
            current_sub_task=step.sub_task,
            executed_code=_head_tail(executed_code),
            returncode=returncode,
            timed_out=timed_out,
            stdout=_render_stdout(stdout),
            stderr=_head_tail(stderr),
        )
    )


def _render_manifest(data_manifest: List[dict], data_manifest_info: dict | None = None,
                     step=None) -> str:
    """The OUTPUT FILES section: what was checked, how far, and what stdout's paths are.

    Says exactly what was scanned ("no file in the run directory was written or modified
    by this attempt (checked recursively; ...)") rather than "(no output files were
    produced)", which was false for every file written outside the top level of data/.
    The step's declared outputs are listed first when the manifest is too long to show
    whole."""
    from cmbagent_lg.self_debug.attempt_manifest import render_for_evaluator

    priority: List[str] = []
    if step is not None:
        try:
            from cmbagent_lg.self_debug.output_postcondition import declared_output_paths

            priority = declared_output_paths(step)
        except Exception:  # noqa: BLE001 — ordering only; never break the prompt
            priority = []
    return render_for_evaluator(data_manifest or [], data_manifest_info, priority)


def _render_carried_over(data_manifest_info: dict | None,
                         input_dir_empty: bool = False) -> str:
    """The FILES CARRIED OVER section, with the two newlines that separate it from the
    OUTPUT FILES block it follows, or "" (the prompt is then unchanged). With the
    empty-data/ fact shown, it says the carried-over files were generated within the
    step, so "carried over" does not read as "provided"."""
    from cmbagent_lg.self_debug.attempt_manifest import render_carried_over

    text = render_carried_over(data_manifest_info, input_dir_empty=input_dir_empty)
    return "\n\n" + text if text else ""


def _render_step_history(step_feedback_history: List[str]) -> str:
    if not step_feedback_history:
        return "(this is the first attempt — no prior feedback on this step)"
    return "\n".join(step_feedback_history)


def escalation_instructions(
    ctx: PlanContext,
    step: Step,
    code_path: str,
    stderr: str,
    reason: str,
) -> str:
    """Prompt for the Claude Agent SDK escalation agent."""
    return ESCALATION_YAML["instructions"].format_map(
        SafeDict(
            escalation_append_instructions=ctx.escalation_append_instructions,
            current_sub_task=step.sub_task,
            code_path=code_path,
            stderr=_head_tail(stderr),
            reason=reason,
        )
    )


def step_evaluator_instructions(
    ctx: PlanContext,
    step: Step,
    stdout: str,
    data_manifest: List[dict],
    step_feedback_history: List[str],
    input_dir_empty: bool | None = None,
    code: str | None = None,
    data_manifest_info: dict | None = None,
) -> str:
    # The data-integrity rules (fabricated/absent input, wrong column, non-converged fit →
    # not fulfilled) are gated by `safe_loading` alone (default True), independent of
    # `enable_data_fixer` and `enable_preflight`. `safe_loading=False` renders them empty.
    data_integrity_rules = (
        STEP_EVALUATOR_YAML.get("data_integrity_rules", "")
        if getattr(ctx, "safe_loading", True)
        else ""
    )
    # Only when data/ really was empty before the run, in two halves: the RULE (what to
    # conclude) beside the other integrity rules, and the FACT block placed right above
    # the stdout it qualifies. The fact needs that prominence — buried in the rule list it
    # loses to the base prompt's "don't fail what you can't verify", which is how a
    # laundered run (earlier attempt writes the file, last attempt "loads" it) slips
    # through. Both render "" otherwise, leaving the prompt untouched.
    input_dir_state = ""
    if data_integrity_rules and input_dir_empty:
        data_integrity_rules += "\n" + STEP_EVALUATOR_YAML.get("empty_input_dir_rule", "")
        # Trailing newline separates this from the STDOUT header the template appends
        # directly after the placeholder; empty, the prompt must have no stray blank line.
        input_dir_state = STEP_EVALUATOR_YAML.get("input_dir_empty_fact", "") + "\n"
    # Two rules about WHAT COUNTS AS A FINISHED STEP, both measured on the metabric
    # fleet as causes of a step arguing itself to death:
    #   negative_result_rules — a bullet phrased as an outcome ("show SMDs below 0.1")
    #     is a requirement to MEASURE, so an honest unwelcome value completes the step.
    #     Project1/Iteration2 step 5 was refused 8× because three of twenty SMDs stayed
    #     above the threshold.
    #   infeasible_rules — the escape hatch (`StepVerdict.infeasible_requirements`) for
    #     a method or column that does not exist here. Project3/Iteration1 step 4 was
    #     refused 8× for not using a Fine-Gray implementation no installed library has.
    # Engineer steps only — this template is the self_debug one, which only the engineer
    # path renders, and the check keeps that true for a caller driving the graph with
    # another agent's step. Always present otherwise: a rule the evaluator is not shown
    # is a rule it does not apply.
    _engineer_step = getattr(step, "sub_task_agent", "engineer") == "engineer"
    infeasible_rules = (
        STEP_EVALUATOR_YAML.get("infeasible_rules", "") if _engineer_step else ""
    )
    negative_result_rules = (
        STEP_EVALUATOR_YAML.get("negative_result_rules", "") if _engineer_step else ""
    )
    return STEP_EVALUATOR_YAML["instructions"].format_map(
        SafeDict(
            evaluator_append_instructions=evaluator_append_block(ctx),
            current_sub_task=step.sub_task,
            current_instructions="\n".join(f"- {b}" for b in step.bullet_points),
            executed_code=_render_code(code),
            stdout=_render_stdout(stdout),
            data_manifest=_render_manifest(data_manifest, data_manifest_info, step),
            carried_over_files=_render_carried_over(data_manifest_info, bool(input_dir_state)),
            step_feedback_history=_render_step_history(step_feedback_history),
            data_integrity_rules=data_integrity_rules,
            infeasible_rules=infeasible_rules,
            negative_result_rules=negative_result_rules,
            input_dir_state=input_dir_state,
        )
    )
