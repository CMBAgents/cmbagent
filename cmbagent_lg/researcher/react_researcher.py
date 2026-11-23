"""ReAct report-writing researcher — the grounded alternative to `researcher`.

The plain node's entire evidence base is `previous_steps_execution_summary`:
prior steps' code, whatever they *printed*, and a manifest of file paths with no
contents — so a value that only ever reached `data/fit_params.csv` has to be
invented. This node gives the researcher READ-ONLY access to the workspace it is
writing about (`list_workspace`, `read_file`, `view_image`) via `bind_tools`.

Design constraints:
  * **Gated.** Selected only when `PlanContext.researcher_mode == "react"`.
  * **Bounded.** At most `ctx.researcher_max_tool_calls` (+ one per prior step)
    tool-call round-trips, each an extra LLM call, before the report is written.
  * **Read-only + sandboxed.** Every tool only reads, is confined to `work_dir`,
    is size-capped, and NEVER raises — it returns a short diagnostic string.
  * **Non-fatal.** Any exception in the loop falls back to the plain researcher.

Downstream contract is unchanged: the node returns `{"attempts", "current_report",
"report_path"}` and writes `reports/step_{N}.md` exactly like `researcher`. The
tool transcript is persisted as `logs/step_{N}_researcher_tools.json` — the audit
trail of what the report was actually grounded in.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.prompt_utils import flatten_content
from cmbagent_lg.researcher.nodes import (  # reuse plain path / fallback
    _proposer,
    _reports_dir,
    claim_retry_feedback,
    researcher,
)
from cmbagent_lg.researcher.grounding import knob, truncation_marker
from cmbagent_lg.researcher.prompts import render_retry_context, researcher_instructions
from cmbagent_lg.researcher.state import ResearcherState
from cmbagent_lg.timing import timed_node
from cmbagent_lg.vlm.images import _IMG_EXTS, collect_images, with_images


# Appended to the standard researcher system prompt. Deliberately general: it
# says "open what you are asked to report on", not "read fit_params.csv".
_REACT_TOOL_GUIDANCE = """
---
**Workspace tools (READ-ONLY).**

The analysis you are writing up left its artifacts on disk. You can look at them:

- `list_workspace()` — every file the run has produced, with sizes.
- `read_file(path, offset=0)` — the contents of a text artifact (CSV, JSON, log,
  report, script). A large file comes back as a PAGE with a footer saying which
  character ranges you saw and which were omitted; `read_file(path, offset=N)`
  shows the omitted part. A value not on the page you saw may be in the omitted
  range — page to it before saying a value is not in the file.
- `view_image(path)` — a careful, quantitative reading of a figure by a vision
  model: axes, scales, ranges, and where each visible feature sits.

How to use them:
- Before writing, ask yourself which numbers and figure features the sub-task
  requires. For each one, identify where it came from. If it was printed in the
  previous steps' output, you already have it. **If it is not there, OPEN THE
  FILE that holds it** — the manifest above gives the exact paths.
- Reading is cheap and it is the point of this step; guessing is not an option.
  Whatever holds the numbers the report needs — a results table, a metrics or
  stats file, a run log, an earlier step's report: read it.
- Figures attached to this message are a low-resolution preview, good for
  context only. To state ANY quantitative feature of a figure — where a peak
  sits, what a level or range is, where a curve crosses — call `view_image` on
  that figure and report the values it gives you. Eyeballing a coordinate off
  the preview and rounding it to something tidy is exactly the kind of
  plausible-but-wrong number this report must not contain.
- Never describe a plot that neither `view_image` nor an attached figure has
  shown you.
- Inspection is bounded; make a few targeted calls, then write the report. Once
  you have what you need, reply with the report itself and no further tool calls.
- If a needed value genuinely is not in any artifact you can read, say so in the
  report and move on. Do NOT fill the gap with a plausible-looking number.

When you are done inspecting, your next reply is the markdown report — first
line of the reply IS the first line of the report, no preamble, no tool calls.
"""


# Cap on what one `read_file` returns: a parameter table or stats JSON fits in
# full, head+tail beyond that so a stray 200 MB CSV can't blow up the prompt.
# `_READ_HEAD` is the fallback page size when the context carries no
# `researcher_read_page_chars`; the tail is a quarter of the page.
_READ_HEAD = 8_000
_READ_TAIL = 2_000
_MAX_LIST = 200
# Per-source excerpt kept in the audit log — it records not just WHICH artifacts
# the report was grounded in but WHAT they said, so a number can be traced back.
# Kept one release for older readers of the tools log; the graders now re-read
# the file from disk (`grounding.render_evidence`) and only fall back to it.
_AUDIT_CHARS = 4_000
# The short head stored beside `output` for the evidence stubs.
_EXCERPT_CHARS = 1_200

# Model `view_image` reads figures with when `vlm_model` is unset. Deliberately
# NOT the lite default: reading a coordinate off an axis is a precision task the
# lite model measurably fails (it rounds peaks to tidy numbers).
_DEFAULT_VIEW_IMAGE_MODEL = "gemini-3.6-flash"


def _head_tail(text: str, head: int, tail: int, path: Optional[str] = None) -> str:
    """Head + marker + tail. The marker names the cut range and the file, so no
    window is ever an unmarked prefix (the bug behind the false-fabrication
    livelock: a grader cannot tell "not in this excerpt" from "not in the file")."""
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    marker = truncation_marker(omitted, head, len(text) - tail, len(text), path)
    return f"{text[:head]}\n\n{marker}\n\n{text[-tail:]}"


def _page(text: str, offset: int, page: int, tail: int):
    """One `read_file` window: (body, ranges, footer).

    The first call (offset 0) returns head + tail so a file's summary lines at
    the end are seen too; a later call returns the page starting at `offset`.
    The footer always states the ranges shown and omitted, and how to reach the
    rest, because the researcher must be able to page to a value the graders
    will later find in the full file."""
    n = len(text)
    if offset <= 0:
        if n <= page + tail:
            return text, [[0, n]], ""
        a, b = page, n - tail
        body = f"{text[:a]}\n\n{truncation_marker(b - a, a, b, n)}\n\n{text[b:]}"
        footer = (f"[showing chars 0–{a} and {b}–{n} of {n}; omitted {a}–{b} — "
                  f"read_file(path, offset={a}) shows them]")
        return body, [[0, a], [b, n]], footer
    a = min(offset, n)
    b = min(a + page, n)
    body = text[a:b]
    omitted = []
    if a > 0:
        omitted.append(f"0–{a}")
    if b < n:
        omitted.append(f"{b}–{n} — read_file(path, offset={b}) shows the next page")
    footer = f"[showing chars {a}–{b} of {n}" + (f"; omitted {', '.join(omitted)}" if omitted else "") + "]"
    return body, [[a, b]], footer


# ── read-only, sandboxed workspace tools ──────────────────────────────────
#
# Built fresh per node call as closures over the run's work_dir so the model can
# pass a manifest path ("data/fit_params.csv") or a bare name ("stats.json").
# Every tool returns a diagnostic string instead of raising. Their docstrings are
# the model-facing tool descriptions — do not shorten them.


def _make_tools(work_dir: Optional[Path], ctx: PlanContext, seen: List[dict]):
    def _resolve(path: str) -> Optional[Path]:
        """Resolve a model-supplied path to a real file INSIDE work_dir, else None."""
        if work_dir is None or not path:
            return None
        raw = Path(path)
        candidates = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates += [work_dir / raw]
            candidates += [work_dir / sub / raw.name
                           for sub in ("data", "reports", "codebase", "logs")]
        root = work_dir.resolve()
        for c in candidates:
            try:
                rc = c.resolve()
            except Exception:  # noqa: BLE001
                continue
            if (root == rc or root in rc.parents) and rc.is_file():
                return rc
        return None

    def list_workspace() -> str:
        """List every file the analysis has produced, with its size in bytes, so you
        know exactly which artifacts you can read."""
        try:
            if work_dir is None or not work_dir.is_dir():
                return "No workspace directory is available."
            rows = []
            for sub in ("data", "reports", "codebase", "logs"):
                d = work_dir / sub
                if not d.is_dir():
                    continue
                files = sorted(p for p in d.iterdir() if p.is_file())
                if not files:
                    continue
                rows.append(f"{sub}/:")
                rows += [f"  - {sub}/{p.name} ({p.stat().st_size} bytes)"
                         for p in files[:_MAX_LIST]]
            # Outputs written elsewhere in the run directory (answer.json, work/,
            # nested data/), from the same bounded walk as the attempt manifest.
            try:
                from cmbagent_lg.self_debug.attempt_manifest import other_workspace_files

                others, truncated = other_workspace_files(work_dir)
            except Exception:  # noqa: BLE001
                others, truncated = [], None
            if others or truncated:
                rows.append("other files:")
                rows += [f"  - {rel} ({size} bytes)" for rel, size in others[:_MAX_LIST]]
                if len(others) > _MAX_LIST:
                    rows.append(f"  - ... and {len(others) - _MAX_LIST} more, not listed")
                if truncated:
                    rows.append(f"  - (listing incomplete: {truncated})")
            return "\n".join(rows) if rows else "The workspace contains no files."
        except Exception as e:  # noqa: BLE001
            return f"list_workspace error: {e}"

    page_chars = max(500, int(knob(ctx, "researcher_read_page_chars", _READ_HEAD)))
    tail_chars = max(100, page_chars // 4)

    def read_file(path: str, offset: int = 0) -> str:
        """Read a text artifact (CSV, JSON, log, markdown, script) and return its
        contents. A small file comes back whole. A large file comes back as a
        PAGE: the first call shows the head and the tail, a call with `offset`
        shows the page starting at that character. The footer says exactly which
        character ranges you saw and which were omitted — a value not on this
        page may be in the omitted range; page to it before abstaining.

        `path` may be a manifest path ('data/fit_params.csv') or a bare filename."""
        try:
            p = _resolve(path)
            if p is None:
                return (f"'{path}' is not a readable file inside the workspace. "
                        f"Call list_workspace() to see what exists.")
            if p.suffix.lower() in _IMG_EXTS:
                return (f"'{path}' is an image, not text — call view_image('{path}') "
                        f"to look at it.")
            raw = p.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("latin-1")
                except Exception:  # noqa: BLE001
                    return (f"'{p.name}' is binary and cannot be read as text "
                            f"({len(raw)} bytes).")
            if "\x00" in text[:4096]:
                return f"'{p.name}' looks binary ({len(raw)} bytes) — not readable as text."
            try:
                off = max(0, int(offset or 0))
            except (TypeError, ValueError):
                off = 0
            if off and off >= len(text):
                return (f"offset {off} is beyond the end of '{path}' "
                        f"({len(text)} chars); the file has no more pages.")
            body, ranges, footer = _page(text, off, page_chars, tail_chars)
            # Provenance, not a copy: the graders re-read the file from disk and
            # check it is still the file the researcher saw (sha256); `ranges`
            # says which part it saw, so an evidence stub can say "the
            # researcher saw chars 0–8000 of 11996" instead of pretending the
            # excerpt was the file. `output` stays one release for old readers.
            seen.append({"tool": "read_file", "path": str(p), "bytes": len(raw),
                         "chars": len(text), "sha256": hashlib.sha256(raw).hexdigest(),
                         "ranges": ranges, "excerpt": text[:_EXCERPT_CHARS],
                         "output": body[:_AUDIT_CHARS]})
            out = f"Contents of {path} ({len(raw)} bytes):\n{body}"
            if footer:
                out += f"\n{footer}"
            return out
        except Exception as e:  # noqa: BLE001
            return f"read_file error on '{path}': {e}"

    def view_image(path: str) -> str:
        """Look at a figure with a vision model and return a factual description of
        what it shows (axes, ranges, features and where they sit)."""
        try:
            p = _resolve(path)
            if p is None:
                return (f"'{path}' is not a readable file inside the workspace. "
                        f"Call list_workspace() to see what exists.")
            if p.suffix.lower() not in _IMG_EXTS:
                return (f"'{path}' is not a viewable image "
                        f"(supported: {', '.join(_IMG_EXTS)}).")
            content = with_images(
                "Describe this figure factually and quantitatively, for someone who "
                "cannot see it. Cover: what is plotted on each axis (including units "
                "and whether the scale is linear or log), the range each axis spans, "
                "and every visible feature — peaks, trends, thresholds, outliers, "
                "multiple series — with the approximate coordinates at which it sits, "
                "read off the axes. Locate each feature by interpolating between the "
                "tick marks it falls between — do NOT snap it to the nearest tick or "
                "to a tidy round number — and where you are unsure, give the interval "
                "you are confident of rather than one arbitrary value inside it. State "
                "clearly if a feature you would expect (a legend, a second series, a "
                "trend) is absent. Report ONLY what is visible in the image; never "
                "infer or fill in a value you cannot read off the plot, and say so "
                "when a value is not legible.",
                [p],
            )
            if isinstance(content, str):  # image unreadable / oversized
                return f"'{p.name}' could not be attached as an image."
            model = chat_model(
                getattr(ctx, "vlm_model", None) or _DEFAULT_VIEW_IMAGE_MODEL, "critic"
            )
            msg = model.invoke(
                [HumanMessage(content=content)],
                config={"tags": ["researcher_view_image"]},
            )
            desc = flatten_content(msg.content).strip()
            seen.append({"tool": "view_image", "path": str(p), "chars": len(desc),
                         "output": desc[:_AUDIT_CHARS]})
            return f"Description of {path} (read off the actual image):\n{desc}"
        except Exception as e:  # noqa: BLE001
            return f"view_image error on '{path}': {e}"

    # `vlm_enabled=False` must mean NO vision calls, not just "skip the preview":
    # with VLM off the researcher grounds numbers from text artifacts and
    # abstains on figure-only quantities.
    tools = [list_workspace, read_file]
    if getattr(ctx, "vlm_enabled", False):
        tools.append(view_image)
    return tools


# ── extra tools (PlanContext.researcher_extra_tools) ─────────────────────
#
# A domain library the report can be checked against (e.g. a literature index):
# loaded from "module:factory" specs, each factory returning plain functions.
# Every call is wrapped like the workspace tools: it never raises, its result is
# capped, and it is recorded in the audit log, so the graders see the published
# numbers the report compares with. A spec that fails to load is skipped with a
# warning, once per process (a worker must keep writing reports without it).

_EXTRA_LOADED: dict = {}


def _load_extra(spec: str):
    """(functions, guidance) for one "module:factory" spec, cached per process."""
    if spec in _EXTRA_LOADED:
        return _EXTRA_LOADED[spec]
    import importlib
    try:
        mod_name, _, attr = spec.partition(":")
        mod = importlib.import_module(mod_name.strip())
        fns = list(getattr(mod, attr.strip() or "tools")())
        out = ([f for f in fns if callable(f)], str(getattr(mod, "TOOL_GUIDANCE", "") or ""))
    except Exception as e:  # noqa: BLE001
        print(f"[react_researcher] WARNING: extra tools {spec!r} not loaded: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        out = ([], "")
    _EXTRA_LOADED[spec] = out
    return out


def _wrap_extra(fn: Callable, cap: int, seen: List[dict]) -> Callable:
    import functools

    @functools.wraps(fn)
    def call(*args, **kwargs):
        name = fn.__name__
        try:
            out = str(fn(*args, **kwargs))
        except Exception as e:  # noqa: BLE001
            out = f"{name} error: {type(e).__name__}: {e}"
        if len(out) > cap:
            out = (out[:cap] + f"\n\n[{name}: result cut at {cap} of {len(out)} chars; "
                   f"narrow the request to see the rest]")
        try:
            label = json.dumps(kwargs, sort_keys=True, default=str)[:300]
        except Exception:  # noqa: BLE001
            label = "?"
        seen.append({"tool": name, "path": f"{name}:{label}", "chars": len(out),
                     "output": out[:_AUDIT_CHARS]})
        return out
    return call


def _extra_tools(ctx: PlanContext, seen: List[dict], taken: set):
    """(wrapped functions, guidance text) for ctx.researcher_extra_tools."""
    specs = [s.strip() for s in str(getattr(ctx, "researcher_extra_tools", "") or "").split(",") if s.strip()]
    cap = max(1000, int(getattr(ctx, "researcher_extra_tool_chars", 12_000) or 12_000))
    tools, guidance = [], []
    for spec in specs:
        fns, g = _load_extra(spec)
        for f in fns:
            if f.__name__ in taken:
                print(f"[react_researcher] WARNING: extra tool {f.__name__!r} from {spec!r} "
                      f"clashes with a tool already bound; skipped", file=sys.stderr, flush=True)
                continue
            taken.add(f.__name__)
            tools.append(_wrap_extra(f, cap, seen))
        if fns and g:
            guidance.append(g.strip())
    return tools, "\n\n".join(guidance)


# ── the node ──────────────────────────────────────────────────────────────


@timed_node("react_researcher")
def react_researcher(
    state: ResearcherState, runtime: Runtime[PlanContext]
) -> ResearcherState:
    """Bounded ReAct report writer. Bumps `attempts`, may call READ-ONLY
    workspace tools to ground the numbers/figures it reports, then writes the
    markdown report. Non-fatal: any error falls back to the plain `researcher`."""
    ctx = runtime.context
    attempts = state.get("attempts", 0) + 1

    try:
        step = state["step"]
        work_dir = Path(state["work_dir"]).expanduser() if state.get("work_dir") else None

        last_report = last_step_unmet = last_step_feedback = None
        last_unsupported = last_suggestions = None
        if attempts > 1:
            last_report = state.get("current_report")
            prior_verdict = state.get("current_step_verdict")
            if prior_verdict is not None:
                last_step_unmet = prior_verdict.unmet_requirements
                last_step_feedback = prior_verdict.feedback
            last_unsupported, last_suggestions = claim_retry_feedback(state)

        system = researcher_instructions(
            ctx,
            step,
            render_retry_context(
                attempts=attempts,
                max_attempts=ctx.max_n_attempts,
                last_report=last_report,
                last_step_unmet=last_step_unmet,
                last_step_feedback=last_step_feedback,
                last_unsupported_claims=last_unsupported,
                last_claim_suggestions=last_suggestions,
            ),
            previous_steps_execution_summary=state.get("previous_steps_execution_summary") or "",
        ) + "\n" + _REACT_TOOL_GUIDANCE

        user = (
            "Write the markdown report addressing the current sub-task and every "
            "bullet-point requirement. First read whatever artifacts you need to "
            "source the values and figure descriptions the report must contain, "
            "then output only the report body — no preamble."
        )

        # The figures ride along on the first call, as in the plain node, but
        # framed as a PREVIEW rather than as the measurement: the plain node's
        # wording would contradict the tool guidance and invite the model to read
        # coordinates off the low-resolution attachment.
        user_content = user
        if getattr(ctx, "vlm_enabled", False):
            images = collect_images(state.get("work_dir"), getattr(ctx, "vlm_max_images", 8))
            if images:
                user_content = with_images(
                    user + " The figures produced by the analysis are attached below "
                    "as a PREVIEW, so you can see which plots exist and what they are "
                    "broadly about. Do not read coordinates off them: to state where a "
                    "feature sits, or any other quantitative property of a figure, call "
                    "`view_image` on that figure and report what it gives you.",
                    images,
                )

        seen: List[dict] = []
        tools = _make_tools(work_dir, ctx, seen)
        extra, extra_guidance = _extra_tools(ctx, seen, {t.__name__ for t in tools})
        tools += extra
        if extra_guidance:
            messages_system_extra = (
                "\n---\n**Reference tools (READ-ONLY).** Besides the workspace you "
                "can consult the library below. Use it to set this analysis's "
                "numbers against published ones; attribute every number taken from "
                "it to its source, with the address the tool prints, and never "
                "present it as a result of this analysis.\n\n" + extra_guidance)
            system = system + messages_system_extra
        tool_map: dict[str, Callable] = {t.__name__: t for t in tools}

        base = _proposer(ctx)  # chat_model(researcher_model, "generator")
        bound = base.bind_tools(tools)

        messages: list = [SystemMessage(system), HumanMessage(content=user_content)]
        # The budget scales with how many prior steps exist: once older steps are
        # compacted to headers, a final synthesis may need one `read_file` per
        # compacted step to recover its findings. This is a CEILING, not a target
        # — a step whose carryover already holds everything stops early — and it
        # stays bounded so a misbehaving loop still terminates.
        base_calls = max(0, int(getattr(ctx, "researcher_max_tool_calls", 6)))
        n_prior_steps = max(0, int(state.get("step_number", 1)) - 1)
        max_calls = base_calls + min(n_prior_steps, 24)
        if extra:
            max_calls += max(0, int(getattr(ctx, "researcher_max_extra_tool_calls", 6) or 0))
        n_tool_roundtrips = 0
        n_llm_calls = 0
        ai: Optional[AIMessage] = None

        for _ in range(max_calls + 1):
            ai = bound.invoke(messages, config={"tags": ["react_researcher"]})
            n_llm_calls += 1
            messages.append(ai)
            tool_calls = getattr(ai, "tool_calls", None) or []
            if not tool_calls:
                break
            n_tool_roundtrips += 1
            if n_tool_roundtrips > max_calls:
                # Budget exhausted: strip the pending tool requests and force a
                # final report (no tools bound) so we never dead-end.
                messages.pop()  # drop the tool-calling AIMessage
                messages.append(HumanMessage(
                    "You have used all available inspection calls. Do NOT call any "
                    "more tools. Write the report now, from what you have actually "
                    "read — for anything you could not source, say where it lives "
                    "rather than stating a value."
                ))
                ai = base.invoke(messages, config={"tags": ["react_researcher"]})
                n_llm_calls += 1
                break
            for tc in tool_calls:
                fn = tool_map.get(tc.get("name", ""))
                args = tc.get("args", {}) or {}
                result = fn(**args) if fn else f"unknown tool: {tc.get('name')!r}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc.get("id", "")))

        report = flatten_content(ai.content if ai is not None else "").strip()
        if not report:
            # The model stopped without prose (e.g. only tool calls) — ask once
            # more, plainly, for the report.
            messages.append(HumanMessage(
                "Now write the markdown report, as instructed above. Output only "
                "the report body."
            ))
            ai = base.invoke(messages, config={"tags": ["react_researcher"]})
            n_llm_calls += 1
            report = flatten_content(ai.content).strip()
        n = state.get("step_number", 1)

        # Audit trail: what this attempt read. Written BEFORE the empty-report
        # guard below — an attempt that spends tool calls and yields no prose
        # otherwise records nothing, and its reads vanish from the cross-attempt
        # union the evaluator grounds on (see `_read_sources`). Keyed by ATTEMPT
        # because a retry reads different artifacts; the unsuffixed name is kept
        # as the "final attempt" pointer for consumers expecting a single path.
        raw = state.get("work_dir")
        if raw:
            record = json.dumps(
                {"attempt": attempts, "tool_roundtrips": n_tool_roundtrips,
                 "llm_calls": n_llm_calls, "sources_read": seen}, indent=2)
            try:
                logs = Path(raw).expanduser() / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                (logs / f"step_{n}_researcher_tools_attempt{attempts}.json").write_text(record)
                (logs / f"step_{n}_researcher_tools.json").write_text(record)
            except OSError:  # noqa: BLE001 — the audit file must never break a run
                pass

        if not report:
            raise RuntimeError("react_researcher produced empty content")

        report_path = ""
        reports = _reports_dir(state)
        if reports is not None:
            out = reports / f"step_{n}.md"
            out.write_text(report)
            report_path = str(out)

        print(f"[react_researcher] attempt {attempts}: {n_tool_roundtrips} tool "
              f"round-trip(s), {n_llm_calls} LLM call(s), "
              f"{len(seen)} artifact(s) read", file=sys.stderr, flush=True)
        return {"attempts": attempts, "current_report": report, "report_path": report_path}

    except Exception as e:  # noqa: BLE001 — non-fatal: fall back to the plain node
        print(f"[react_researcher] non-fatal error ({e!r}) → falling back to plain "
              f"researcher", file=sys.stderr, flush=True)
        return researcher(state, runtime)


__all__ = ["react_researcher"]
