"""Deterministic pre-flight file inspection.

Before the FIRST engineer attempt, if `work_dir/data/` holds input file(s), this
node inspects them WITHOUT any model call and injects a factual FILE REPORT into
the engineer's context, so the loader is written from FACTS (magic bytes,
delimiter, HDF5 keys, dtypes) rather than a fault-specific checklist.

When `data/` is EMPTY there are no bytes to report and a different question
matters: was input supposed to be there? That is the INTENT GATE
(`PlanContext.enable_intent_gate`) — ONE cheap classifier call, made only in that
case, whose answer arms the engineer's do-not-fabricate directive and the
router's terminal guard. See `_classify_intent`.

"Empty" means NO PROVIDED INPUT IS REACHABLE, not merely "work_dir/data has no
files": before the gate is even considered, `resolve_named_input` scans the
step's wording AND the run-level wording (`PlanContext.main_task`,
`PlanContext.engineer_append_instructions`) for a data-file path that already
exists on disk — an absolute path, a `~` path, a `<basename> from </abs/dir/>`
pair, or a bare basename that lives under a directory the text names. A caller
such as Denario passes its data description as `main_task`, so the absolute
dataset path usually lives THERE rather than in the planner's bullet ("Load the
`qm9.csv` dataset"). Measured on the qm9-joint fleet: 231/465 iterations halted
at step 1 because only the step text was scanned, the load from the absolute
path succeeded, and the clean success was then called fabrication.

Design contract:
  * **Zero model calls on the normal path.** Pure `csv.Sniffer` / `h5py` /
    `np.load` / `json` inspection; the intent-gate call is the one exception and
    fires only when `data/` is empty.
  * **Never raises.** Every probe is guarded; a failed probe is omitted and the
    report degrades gracefully (worst case: name + size + first bytes).
  * **Purely factual.** It reports WHAT the bytes are, never which reader arg to
    pass — that stays the engineer's judgement, so no fault is hard-coded.
  * **Bounded output** (~40 lines): at most a few files, a few lines each.

Gated on `PlanContext.enable_preflight` (default True), which wires
START → preflight → engineer; off → START → engineer. Runs once, since START
fires once and retries loop back to the engineer.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.self_debug.schemas import IntentVerdict
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Output caps — keep the injected report small and prompt-friendly.
_MAX_FILES = 5
_MAX_HEAD_LINES = 5
_MAX_HDF5_ENTRIES = 20
_MAX_TOTAL_LINES = 40


# (signature, human description). Ordered; first match wins.
_MAGIC = [
    (b"\x1f\x8b", "gzip-compressed"),
    (b"\x89HDF\r\n\x1a\n", "HDF5"),
    (b"\x93NUMPY", "npy array"),
    (b"PK\x03\x04", "zip container (npz / parquet / xlsx / zip)"),
    (b"BZh", "bzip2-compressed"),
]


def _detect_magic(raw: bytes) -> Optional[str]:
    """Type from the leading bytes, or None for plain/undetected text."""
    for sig, desc in _MAGIC:
        if raw.startswith(sig):
            return desc
    stripped = raw.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "JSON text"
    return None


def _inspect_text(sample_text: str, out: list) -> None:
    """Append factual observations about a delimited/plain-text sample:
    first lines verbatim, comment preamble, sniffed delimiter, header line,
    and decimal/thousands hints. Purely descriptive — no reader-arg advice."""
    lines = sample_text.splitlines()
    head = lines[:_MAX_HEAD_LINES]
    out.append("first lines (verbatim):")
    for ln in head:
        out.append(f"    {ln!r}")

    # leading '#'-comment preamble?
    n_comment = 0
    for ln in lines:
        if ln.lstrip().startswith("#"):
            n_comment += 1
        else:
            break
    if n_comment:
        out.append(f"comment preamble: {n_comment} leading '#' line(s) before the header")

    # header = first non-comment, non-blank line
    header = next((ln for ln in lines[n_comment:] if ln.strip()), "")
    if header:
        out.append(f"header line: {header!r}")

    # sniff delimiter on the first ~20 body lines
    body = "\n".join(lines[n_comment:n_comment + 20])
    delimiter = None
    if body.strip():
        try:
            dialect = csv.Sniffer().sniff(body, delimiters=",;\t| ")
            delimiter = dialect.delimiter
            out.append(f"csv.Sniffer delimiter: {dialect.delimiter!r}")
        except Exception:  # noqa: BLE001
            out.append("csv.Sniffer: could not determine a delimiter")

    # decimal / thousands numeric-format hint (factual observation only). Meaningless on a
    # comma-DELIMITED file, where `1,2` is two fields: on such a file it read as a
    # thousands/decimal-comma hint on every row (seen on the fleet's qm9.csv).
    first_data = next((ln for ln in lines[n_comment + 1:] if ln.strip()), "")
    if delimiter != "," and re.search(r"\d,\d", first_data):
        out.append(f"numeric format: comma appears inside numbers, e.g. {first_data[:60]!r}")


def _inspect_one(p: Path) -> list:
    """Factual report lines for one file. Never raises."""
    try:
        size = p.stat().st_size
    except Exception:  # noqa: BLE001
        size = -1
    out = [f"- {p.name} ({size} bytes)"]

    try:
        raw = p.read_bytes()[:8192]
    except Exception as e:  # noqa: BLE001
        out.append(f"  (could not read: {e})")
        return out

    if not raw:
        out.append("  EMPTY (0 bytes read) — no content to load")
        return out

    out.append(f"  first bytes: {raw[:32]!r}")
    magic = _detect_magic(raw)
    out.append(f"  detected type: {magic or 'plain/delimited text (no binary signature)'}")

    body_lines: list = []
    try:
        if magic == "HDF5":
            _probe_hdf5(p, body_lines)
        elif magic == "npy array":
            _probe_npy(p, body_lines)
        elif magic and magic.startswith("zip"):
            _probe_zip_npz(p, body_lines)
        elif magic == "gzip-compressed":
            _probe_gzip(p, body_lines)
        elif magic == "JSON text":
            _probe_json(p, body_lines)
        else:
            _probe_plain_text(raw, body_lines)
    except Exception as e:  # noqa: BLE001 — a failed probe is simply omitted
        body_lines.append(f"(inspection probe failed: {e})")

    out.extend(f"  {ln}" for ln in body_lines)
    return out


def _probe_hdf5(p: Path, out: list) -> None:
    import h5py

    tree = []
    with h5py.File(p, "r") as f:
        def _visit(name, obj):
            if len(tree) >= _MAX_HDF5_ENTRIES:
                return
            if isinstance(obj, h5py.Dataset):
                tree.append(f"  {name}  [Dataset shape={obj.shape} dtype={obj.dtype}]")
            else:
                tree.append(f"  {name}  [Group]")
        f.visititems(_visit)
    out.append("HDF5 group/dataset tree:")
    out.extend(tree or ["  (no datasets found)"])


def _probe_npy(p: Path, out: list) -> None:
    import numpy as np

    try:
        arr = np.load(p, allow_pickle=False)
        out.append(f"npy: shape={arr.shape} dtype={arr.dtype} (not pickled)")
    except Exception:
        arr = np.load(p, allow_pickle=True)
        pickled = arr.dtype == object
        out.append(f"npy: shape={arr.shape} dtype={arr.dtype}"
                   f"{' (object/pickled — needs allow_pickle=True)' if pickled else ''}")


def _probe_zip_npz(p: Path, out: list) -> None:
    import numpy as np

    try:
        with np.load(p, allow_pickle=True) as z:
            keys = list(z.files)
        out.append(f"npz archive keys: {keys}")
    except Exception:
        import zipfile
        with zipfile.ZipFile(p) as z:
            out.append(f"zip members: {z.namelist()[:10]}")


def _probe_gzip(p: Path, out: list) -> None:
    import gzip

    with gzip.open(p, "rt", errors="replace") as f:
        text = f.read(4096)
    out.append("gzip-decompressed content:")
    _inspect_text(text, out)


def _probe_json(p: Path, out: list) -> None:
    text = p.read_text(errors="replace")[:8192]
    try:
        obj = json.loads(p.read_text())
        top = type(obj).__name__
        out.append(f"json: parses OK, top-level type = {top}")
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            out.append(f"first record keys: {list(obj[0].keys())}")
        elif isinstance(obj, dict):
            out.append(f"top-level keys: {list(obj.keys())[:12]}")
    except Exception as e:  # noqa: BLE001
        out.append(f"json: does NOT parse — {type(e).__name__}: {e}")
        out.append(f"first bytes of content: {text[:120]!r}")


def _probe_plain_text(raw: bytes, out: list) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        out.append("encoding: not valid UTF-8")
        try:
            text = raw.decode("latin-1")
            out.append("  (decodes as latin-1)")
        except Exception:  # noqa: BLE001
            out.append("  (undecodable as latin-1 either)")
            return
    _inspect_text(text, out)


def inspect_data_dir(data_dir) -> str:
    """Deterministic, factual, bounded inspection of every file in `data_dir`.

    Returns a plain-text FILE REPORT (never raises). Empty string when the dir
    is absent/empty. Facts only: type, first lines, delimiter, keys, dtypes."""
    try:
        d = Path(data_dir).expanduser()
    except Exception:  # noqa: BLE001
        return ""
    if not d.is_dir():
        return ""
    files = sorted(p for p in d.iterdir() if p.is_file())
    if not files:
        return ""

    report_lines: list = []
    for p in files[:_MAX_FILES]:
        report_lines.extend(_inspect_one(p))
    if len(files) > _MAX_FILES:
        report_lines.append(f"- (+{len(files) - _MAX_FILES} more file(s) not shown)")

    # Hard cap on total lines so the injected block stays prompt-friendly.
    if len(report_lines) > _MAX_TOTAL_LINES:
        report_lines = report_lines[:_MAX_TOTAL_LINES] + ["- (report truncated)"]
    return "\n".join(report_lines)


# ── intent gate ─────────────────────────────────────────────────────────────


_INTENT_SYSTEM = """You classify the INTENT of a single sub-task in a data-analysis pipeline.

The workspace directory `data/` is currently EMPTY. Decide, from the sub-task
wording ALONE, which of these two kinds of step it is:

  needs_provided_input = true
    The step is written on the assumption that a data file ALREADY EXISTS and
    was provided to the run: it says to load / read / open / parse / analyse /
    fit / apply "the data in data/", "the provided <thing>", "data/<name>", the
    "given"/"supplied"/"observed"/"measured" dataset, or names an input file it
    does not itself create. The step consumes input it does not produce.

  needs_provided_input = false
    The step CREATES the data it works on — simulate, generate, synthesize,
    sample, draw, compute, construct, download, fetch — and then saves and/or
    analyses what it just made. Also false for a step that needs no input file
    at all (pure computation, a self-contained calculation, plotting a formula).

Decide on the source of the data, not on whether files are written or read: a
step that generates data, saves it, then reads it back is still false, because
nothing was supposed to be provided. A step that loads a provided file and
writes results is true.

If the step both loads a provided file AND generates other data, answer true —
the provided file is still required.

Answer with the boolean plus one short sentence quoting the deciding wording."""


def _classify_intent(ctx: PlanContext, task: str) -> Optional[IntentVerdict]:
    """ONE structured-output call: does `task` require PROVIDED input data?

    FAIL-OPEN — None on any error (bad key, timeout, unparseable answer, missing
    model), and callers treat None exactly like False: the ordinary flow, with no
    directive and no guard.

    Role "classifier", not "formatter": it is a judgement, so a registry that turns the
    formatter's thinking off (an extraction role) leaves it alone; Gemini settings are the
    formatter's.
    """
    try:
        model = chat_model(ctx.intent_gate_model or ctx.formatter_model, "classifier")
        return model.with_structured_output(IntentVerdict).invoke(
            [SystemMessage(_INTENT_SYSTEM), HumanMessage(task)],
            config={"tags": ["intent_gate"]},
        )
    except Exception as e:  # noqa: BLE001 — fail open, never crash the graph
        print(f"[intent_gate] classification skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return None


def _task_text(step) -> str:
    """The sub-task plus its bullet points — the wording the gate judges."""
    bullets = "\n".join(f"- {b}" for b in (getattr(step, "bullet_points", None) or []))
    sub_task = getattr(step, "sub_task", "") or ""
    return f"SUB-TASK:\n{sub_task}\n\nINSTRUCTIONS:\n{bullets}" if bullets else f"SUB-TASK:\n{sub_task}"


REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"


def _remote_data_empty(session: str) -> Optional[bool]:
    """Is the GUEST session's `data/` empty? None when the guest cannot answer.

    Under a remote executor the engine's `work_dir/data` is not the execution dir
    (`nodes._run_remote` ships the step to the guest and the data lives there), so it is
    empty on every step — including one that consumes the previous step's output. One cheap
    listing answers what the local dir cannot.

    FAIL-OPEN, like every other call on this seam: an unanswered listing is UNKNOWN, never
    "empty" — asserting emptiness from a directory that is not the exec dir is exactly the
    fact this replaces. None leaves `input_dir_empty` unset, so the intent gate stays
    disarmed and the step_evaluator's `safe_loading` rule is not rendered.
    """
    try:
        from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

        reply = RemoteExecutor.from_env(os.environ[REMOTE_ENV]).list_files(session)
    except Exception as e:  # noqa: BLE001 — the listing must never break the step
        print(f"[preflight] remote data listing skipped: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None
    if not isinstance(reply, dict) or reply.get("type") != "listing" or "files" not in reply:
        print(f"[preflight] remote data listing unusable: {reply!r}", file=sys.stderr, flush=True)
        return None
    files = reply.get("files") or []
    print(f"[preflight] guest session {session!r} data/: {len(files)} file(s)",
          file=sys.stderr, flush=True)
    return not files


# File-path tokens with a data-ish extension, for the provided-input check below.
_INPUT_PATH_RE = re.compile(
    r"[~./\w\-]*\.(?:csv|tsv|txt|dat|json|jsonl|npz|npy|h5|hdf5|hdf|parquet|pq|"
    r"pkl|pickle|xlsx?|fits|nc|zip|gz|feather|arrow)\b",
    re.IGNORECASE,
)
# Directory-shaped tokens: absolute or `~`-rooted paths, with or without a trailing
# slash ("from /abs/dir/", "under ~/datasets"). A bare basename named anywhere in the
# text is looked up under each of these that exists.
_DIR_PATH_RE = re.compile(r"(?:~|/)[\w.\-]+(?:/[\w.\-]+)*/?")
# Bounds — the scan is pure `stat()` work but the text is model-written and unbounded.
_MAX_PATH_TOKENS = 64
_MAX_DIR_TOKENS = 16
# The remote list is stat()ed one path at a time on the guest, so it is capped again.
_MAX_CANDIDATES = 256


def _run_text(ctx) -> str:
    """Run-level wording the scan must honour alongside the step's own text.

    `main_task` is where a caller such as Denario puts its data description (and hence
    the ABSOLUTE dataset path); `engineer_append_instructions` is the caller's standing
    guidance to the engineer. Neither is visible in the step's sub_task/bullets, which
    is why the step-only scan missed the qm9-joint inputs. Never raises."""
    parts = []
    for attr in ("main_task", "engineer_append_instructions"):
        try:
            v = getattr(ctx, attr, None)
        except Exception:  # noqa: BLE001
            v = None
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return "\n".join(parts)


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _safe_path(tok: str) -> Optional[Path]:
    """`Path(tok).expanduser()` that never raises (a `~user` with no such user does)."""
    try:
        return Path(tok).expanduser()
    except Exception:  # noqa: BLE001
        return None


def _is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:  # noqa: BLE001 — a bad path must never crash pre-flight
        return False


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:  # noqa: BLE001
        return False


def _absolute(p: Path) -> Path:
    """`p` as an absolute path (a relative `work_dir` is relative to the engine's cwd,
    exactly as the executor interprets it). Never raises."""
    try:
        return p if p.is_absolute() else p.absolute()
    except Exception:  # noqa: BLE001
        return p


def _path_tokens(text: str) -> tuple:
    """The file-path and directory-shaped tokens `text` names, deduped and bounded.

    Pure text work — no filesystem call — so the same tokens can be resolved against the
    engine's disk (`resolve_named_input`) or the guest's (`_candidate_paths`)."""
    files = _dedupe(
        m.group(0) for m in _INPUT_PATH_RE.finditer(text)
        if m.group(0) and not m.group(0).startswith(".")  # skip bare ".csv"-style noise
    )[:_MAX_PATH_TOKENS]
    dirs = _dedupe(m.group(0).rstrip("/") for m in _DIR_PATH_RE.finditer(text))[:_MAX_DIR_TOKENS]
    return files, dirs


def resolve_named_input(text: str, work_dir: Optional[Path] = None) -> Optional[Path]:
    """The first data-file path named in `text` that ALREADY EXISTS on disk, else None.

    Existence is the discriminator between a provided INPUT (which pre-exists) and an
    OUTPUT the engineer will create (which does not exist yet at pre-flight time). Three
    spellings are resolved, in this order for each file token:

      1. the token itself — absolute, `~`-rooted, or relative to `work_dir`;
      2. `work_dir/data/<basename>` and `work_dir/<basename>`;
      3. `<dir>/<basename>` for every existing directory the text names — an explicit
         directory token ("qm9.csv from /abs/dir/") or the parent of any absolute file
         path in the text ("The file /abs/dir/qm9.csv contains …" + "Load `qm9.csv`").

    A relative token is NEVER tried against the process cwd: the executor runs the script
    with cwd = `work_dir` (nodes.executor: `run_cwd = codebase.parent`), so a file that
    exists relative to the ENGINE's cwd is unreachable to the script. The fleet worker's
    cwd holds `data/qm9.csv` while every iteration's `work_dir/data` is empty; a step
    saying "Load QM9 from data/qm9.csv" used to resolve to that cwd-relative file, and
    the pre-flight report then told the engineer to load it "from this exact path" — a
    guaranteed FileNotFoundError on attempt 1. The result is always ABSOLUTE for the same
    reason: the report hands it to the engineer verbatim.

    A NAMED-but-ABSENT path resolves to nothing, so genuine fabrication (data/ empty,
    no reachable input named anywhere) keeps tripping the guard. Pure `stat()` work,
    bounded by `_MAX_PATH_TOKENS` / `_MAX_DIR_TOKENS`; never raises.
    """
    if not text:
        return None
    try:
        wd = Path(work_dir).expanduser() if work_dir else None
    except Exception:  # noqa: BLE001
        wd = None

    file_toks, dir_toks = _path_tokens(text)

    # Directories the text names, explicitly or as the parent of an absolute file path.
    dirs: list = []
    for tok in dir_toks:
        d = _safe_path(tok)
        if d is not None and d.is_absolute() and _is_dir(d):
            dirs.append(d)
        if len(dirs) >= _MAX_DIR_TOKENS:
            break
    for tok in file_toks:
        p = _safe_path(tok)
        if p is not None and p.is_absolute() and _is_dir(p.parent):
            dirs.append(p.parent)
    dirs = _dedupe(dirs)[:_MAX_DIR_TOKENS]

    for tok in file_toks:
        p = _safe_path(tok)
        if p is None:
            continue
        # A relative token is meaningful only under work_dir (the script's cwd) — never
        # under the engine's cwd, see the docstring.
        candidates = [p] if p.is_absolute() else ([wd / tok] if wd is not None else [])
        base = p.name
        if base and wd is not None:
            candidates += [wd / "data" / base, wd / base]
        if base:
            candidates += [d / base for d in dirs]
        for c in candidates:
            if _is_file(c):
                return _absolute(c)
    return None


def named_input_for_step(step, work_dir: Optional[Path], ctx=None) -> Optional[Path]:
    """`resolve_named_input` over the step's text PLUS the run-level text. Never raises."""
    try:
        text = _task_text(step) + "\n" + (_run_text(ctx) if ctx is not None else "")
    except Exception:  # noqa: BLE001
        return None
    try:
        return resolve_named_input(text, work_dir)
    except Exception:  # noqa: BLE001
        return None


def _named_input_on_disk(step, work_dir: Path, ctx=None) -> bool:
    """Does the step OR the run text NAME a data-file path that ALREADY EXISTS on disk?

    A provided input at an absolute or mounted path lives OUTSIDE `work_dir/data`, so
    the data/-only emptiness check would misread a legitimate load as fabrication.
    See `resolve_named_input` for what counts as "named".
    """
    return named_input_for_step(step, work_dir, ctx) is not None



# ── the same question, asked on the guest ───────────────────────────────────

def _candidate_paths(text: str, work_dir: Optional[Path]) -> list:
    """`resolve_named_input`'s candidates, AS THE GUEST WOULD SPELL THEM, without
    touching a disk.

    Absolute tokens and `<named dir>/<basename>` joins go over as-is. A RELATIVE token
    stays relative: the guest evaluates it from the session dir, which is the script's
    cwd there — joining it to the engine's `work_dir` would name a path only this
    process has. Directory tokens are NOT pre-filtered by `is_dir`, because that check
    would answer for the wrong filesystem; a join under a directory that does not exist
    is simply never a file.
    """
    if not text:
        return []
    file_toks, dir_toks = _path_tokens(text)
    # `_DIR_PATH_RE` also matches the tail of a relative file token ("data/x.h5" -> "/x.h5"),
    # which the local resolver drops with `is_dir`. That check answers for this filesystem,
    # so here a token that NAMES A DATA FILE is dropped instead — a file is not a directory.
    dirs = []
    for tok in dir_toks:
        tok = tok.rstrip(".")
        if not tok or _INPUT_PATH_RE.fullmatch(tok):
            continue
        d = _safe_path(tok)
        if d is not None and d.is_absolute():
            dirs.append(d)
    for tok in file_toks:
        p = _safe_path(tok)
        if p is not None and p.is_absolute():
            dirs.append(p.parent)
    dirs = _dedupe(dirs)[:_MAX_DIR_TOKENS]

    out: list = []
    for tok in file_toks:
        p = _safe_path(tok)
        if p is None:
            continue
        base = p.name
        if p.is_absolute():
            out.append(str(p))
        elif base:
            out += [tok, f"data/{base}", base]   # resolved from the guest's session dir
        if base:
            out += [str(d / base) for d in dirs]
    return _dedupe(out)[:_MAX_CANDIDATES]


# Executed INSIDE the sandbox being asked about. Self-contained and stdlib-only, like
# `api_introspect.SNIPPET`: it must not depend on this package or on how it travelled.
_NAMED_INPUT_SNIPPET = r"""
import json, os, sys
for p in json.loads(sys.argv[1]):
    try:
        if os.path.isfile(p):
            print(p)
            break
    except OSError:
        pass
"""


def _remote_named_input(session: str, candidates: list) -> Optional[str]:
    """The first candidate that is a file IN THE GUEST SANDBOX; "" when none are; None
    when the guest cannot answer.

    `/datasets` and `/datasets-shared` are gcsfuse mounts on the guest and do not exist
    on the engine, so a local `stat()` reads a provided input as missing — which IS the
    fabrication signature — and a clean load is then failed as fabricated. Measured on
    a remote run: 150 real Iris rows read at exit 0, routed to the
    terminal `missing_input`. `introspect` runs the probe in the same sandbox as
    `execute`, with the same image and the same mounts, so it sees what the step will.

    None and "" both leave the caller's fact alone, so a probe that cannot run can only
    ever cost us this fix, never the guard: the sole effect of a reply is that a CONFIRMED
    file disarms the gate. The two are still distinguished in the log, because "the guest
    says it is not there" and "the guest did not answer" are different operational facts.
    """
    try:
        from cmbagent_lg.self_debug.remote_exec import RemoteExecutor

        reply = RemoteExecutor.from_env(os.environ[REMOTE_ENV]).introspect(
            session, _NAMED_INPUT_SNIPPET, [json.dumps(candidates)], timeout=15
        )
    except Exception as e:  # noqa: BLE001 — the probe must never break the step
        print(f"[preflight] remote input probe skipped: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None
    if not isinstance(reply, dict) or reply.get("type") != "introspect_result" \
            or reply.get("returncode") != 0:
        print(f"[preflight] remote input probe unusable: {reply!r}", file=sys.stderr, flush=True)
        return None
    hit = (reply.get("stdout") or "").strip().splitlines()
    print(f"[preflight] guest probed {len(candidates)} candidate path(s): "
          f"{hit[0] if hit else 'none found'}", file=sys.stderr, flush=True)
    return hit[0] if hit else ""


def _remote_named_input_for_step(step, work_dir: Optional[Path], ctx=None) -> Optional[str]:
    """`named_input_for_step`, answered by the guest. "" when the task names nothing the
    guest can reach (so the gate keeps its teeth), None when the guest cannot answer."""
    try:
        text = _task_text(step) + "\n" + (_run_text(ctx) if ctx is not None else "")
        candidates = _candidate_paths(text, work_dir)
    except Exception:  # noqa: BLE001
        return ""
    if not candidates:
        return ""   # nothing named → nothing to ask; this is a real empty-input step
    return _remote_named_input(Path(work_dir).name, candidates)


# ── the node ────────────────────────────────────────────────────────────────


@timed_node("preflight")
def preflight(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Inspect `work_dir/data/` once, before the first engineer attempt, and
    stash a factual FILE REPORT in state. No model call; never fatal.

    Also records `input_dir_empty`. It MUST be captured here, before any attempt
    runs: a failed attempt can leave a self-created file behind and make a later
    attempt's `data/` look legitimately populated. The step_evaluator uses it (see
    `safe_loading`) to tell a fabricated "load" from a genuine "generate" step.

    When that fact is True and `enable_intent_gate` is on, ONE classifier call
    (`_classify_intent`) decides whether input was supposed to be there. Non-empty
    `data/` → no call at all."""
    raw = state.get("work_dir")
    if not raw:
        return {"preflight_report": ""}
    ctx = runtime.context
    data_dir = Path(raw).expanduser() / "data"
    try:
        report = inspect_data_dir(data_dir)
    except Exception:  # noqa: BLE001 — inspection must never crash the graph
        report = ""
    try:
        # `input_dir_empty` means "no provided input is REACHABLE", not merely
        # "work_dir/data is empty": data/ has no files AND neither the step nor the
        # run text names an existing data-file path elsewhere on disk.
        empty = not data_dir.is_dir() or not any(p.is_file() for p in data_dir.iterdir())
        if empty and os.environ.get(REMOTE_ENV):
            empty = _remote_data_empty(Path(raw).expanduser().name)  # the guest owns the exec dir
        if empty:
            wd = Path(raw).expanduser()
            remote_hit = False
            try:
                named = named_input_for_step(state["step"], wd, ctx)
            except Exception:  # noqa: BLE001 — the extra check must never crash pre-flight
                named = None
            # The engine cannot stat a mount that only exists on the guest, so a miss here
            # is not an answer under a remote executor — ask the side that can see it.
            if named is None and os.environ.get(REMOTE_ENV):
                hit = _remote_named_input_for_step(state["step"], wd, ctx)
                # A probe that could not run adds NOTHING — it must never disarm the guard.
                # Unlike the data/ listing, whose failure leaves the emptiness itself
                # undetermined, here `_remote_data_empty` has already answered: a hiccup on
                # this second question may not turn an anti-fabrication guard off by itself.
                if hit:
                    named, remote_hit = Path(hit), True
            if empty and named is not None:
                empty = False
                print(f"[preflight] data/ is empty but the task names an existing input: "
                      f"{named}{' (guest-side)' if remote_hit else ''} → intent gate NOT armed",
                      file=sys.stderr, flush=True)
                if not report:
                    # Same factual report the engineer gets for files under data/, so the
                    # loader is written from the real bytes — and from the REAL path.
                    try:
                        named_line = [f"- provided input named in the task, found OUTSIDE data/ at: "
                                      f"{named}  (load it from this exact path)"]
                        # `_inspect_one` opens the file; a guest-side path is not ours to read.
                        report = "\n".join(named_line if remote_hit
                                           else named_line + _inspect_one(named))
                    except Exception:  # noqa: BLE001
                        report = ""
    except Exception:  # noqa: BLE001
        empty = None
    out: DebugState = {"preflight_report": report}
    if empty is not None:
        out["input_dir_empty"] = empty

    # INTENT GATE — only worth asking when there is nothing in data/.
    if empty and getattr(ctx, "enable_intent_gate", True):
        try:
            task = _task_text(state["step"])
        except Exception:  # noqa: BLE001 — fail open, same as a failed call
            task = None
        verdict = _classify_intent(ctx, task) if task else None
        if verdict is not None:
            out["needs_provided_input"] = verdict.needs_provided_input
            out["intent_gate_reason"] = verdict.reason
            print(
                f"[intent_gate] data/ is EMPTY → needs_provided_input="
                f"{verdict.needs_provided_input} ({verdict.reason})",
                file=sys.stderr, flush=True,
            )
    return out


__all__ = ["preflight", "inspect_data_dir", "_classify_intent", "_task_text",
           "resolve_named_input", "named_input_for_step"]
