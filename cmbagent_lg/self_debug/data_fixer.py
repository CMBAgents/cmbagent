"""data_loader_fixer node — the self_debug data-LOAD escape hatch.

Two stages. (1) Deterministic inspection of the real file (magic bytes, csv.Sniffer, HDF5
tree, npy dtype) decides the dangerous case with no model: a genuinely ABSENT required
input yields `format="missing"` and a terminal not-fulfilled, never a synthesised loader.
A file the script itself writes first (create-then-read) is NOT missing — it falls through
to a normal retry. (2) For a present-but-mis-parsed file, one structured-output call
produces the loader snippet injected into the next engineer attempt's retry context.

Opt-in (`PlanContext.enable_data_fixer`), one-shot per step (`data_fixed`, mirroring
`escalated`), and non-fatal: any exception is a no-op back to the normal retry loop.
Unlike `escalation`, it has no Anthropic dependency.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from cmbagent_lg.context import PlanContext
from cmbagent_lg.llms import chat_model
from cmbagent_lg.prompt_utils import schema_field_brief
from cmbagent_lg.self_debug.nodes import _logs_dir
from cmbagent_lg.self_debug.schemas import DataLoaderFix, StepVerdict
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Non-lite fallback when neither `data_fixer_model` nor `engineer_model` is set.
_DEFAULT_DATA_FIXER_MODEL = "gemini-3.6-flash"


# ── detection heuristics (also imported by the router in nodes.py) ─────────

# Lower-cased substrings marking a DATA-LOAD / parse fault. Matched against stdout +
# stderr + the evaluator's summary, not just a traceback: the engineer often catches the
# exception and prints it.
_DATA_LOAD_MARKERS = (
    "filenotfounderror", "no such file", "[errno 2]",
    "unicodedecodeerror", "codec can't decode", "codec can’t decode",
    "invalid start byte", "invalid continuation byte",
    "parsererror", "error tokenizing data", "no columns to parse",
    "emptydataerror", "could not convert string to float",
    "unable to parse string", "allow_pickle",
    "object arrays cannot be loaded", "cannot load file containing pickled",
    "failed to interpret", "invalid load key",
    "not a gzipped file", "bad magic number for", "gzip",
    "unable to open file", "unable to open object",
    "unable to synchronously open", "not a dataset", "is a group",
    "no such node", "doesn't exist)", "jsondecodeerror",
    "expecting value", "extra data", "expecting ',' delimiter",
    "expecting property name", "h5py", "keyerror",
)


def _error_text(state: DebugState) -> str:
    """Combined lower-cased error surface: stderr + stdout + evaluator summary."""
    parts = [
        state.get("execution_stderr", "") or "",
        state.get("execution_stdout", "") or "",
    ]
    v = state.get("current_execution_verdict")
    if v is not None:
        parts.append(getattr(v, "error_summary", "") or "")
    return "\n".join(parts).lower()


def looks_like_data_load_error(state: DebugState) -> bool:
    """True if the last failure looks like a data-loading / parsing fault."""
    v = state.get("current_execution_verdict")
    if v is None or getattr(v, "status", None) != "failure":
        return False
    text = _error_text(state)
    return any(m in text for m in _DATA_LOAD_MARKERS)


_FNF_PATH_RE = re.compile(
    r"(?:no such file(?: or directory)?[^']*|filenotfounderror[^']*)'([^']+)'",
    re.IGNORECASE,
)


def looks_like_file_not_found(state: DebugState) -> tuple[bool, Optional[str]]:
    """(is_file_not_found, referenced_path). The path is best-effort — parsed
    from the standard `[Errno 2] No such file or directory: 'data/x.csv'`."""
    text = _error_text(state)
    is_fnf = ("filenotfounderror" in text) or ("no such file" in text)
    if not is_fnf:
        return False, None
    m = _FNF_PATH_RE.search(
        (state.get("execution_stderr", "") or "")
        + "\n" + (state.get("execution_stdout", "") or "")
    )
    return True, (m.group(1) if m else None)


def _data_dir(state: DebugState) -> Optional[Path]:
    raw = state.get("work_dir")
    if not raw:
        return None
    d = Path(raw).expanduser() / "data"
    return d if d.is_dir() else None


def has_input_files(state: DebugState) -> bool:
    """True if `work_dir/data/` currently holds at least one file to inspect."""
    d = _data_dir(state)
    return bool(d and any(p.is_file() for p in d.iterdir()))


def should_data_fix(state: DebugState) -> bool:
    """Router predicate: is this a data-load failure the fixer can act on?

    Fires when the failure looks like a data-load fault AND there is either a
    file to inspect OR a FileNotFoundError to adjudicate (the empty-`data/`
    missing-input case, which the fixer terminates rather than fabricating)."""
    if not looks_like_data_load_error(state):
        return False
    if has_input_files(state):
        return True
    return looks_like_file_not_found(state)[0]


# ── create-then-read guard ────────────────────────────────────────────────

_WRITE_VERBS = (
    "to_csv", "to_parquet", "to_hdf", "to_pickle", "to_excel", "to_json",
    "savetxt", "np.save", "numpy.save", "savez", "save(", "tofile",
    "savefig", "create_dataset", "h5py.file", "excelwriter",
    "pickle.dump", "json.dump", "write_text", "write_bytes", ".write(",
)
_WRITE_MODES = ("'w'", '"w"', "'wb'", '"wb"', "'a'", '"a"', "'x'", '"x"',
                "'ab'", '"ab"', "'w+'", '"w+"')


def _code_writes(code: str, name: str) -> bool:
    """True if `code` appears to WRITE a file named `name` (create-then-read): the
    basename co-occurs on a line with a write verb, or with `open(` in a write mode.
    Deliberately biased toward True — a false positive only declines to call the input
    "missing" and lets the loop retry, which is the safe direction."""
    if not name:
        return False
    base = Path(name).name.lower()
    for line in code.splitlines():
        low = line.lower()
        if base not in low:
            continue
        if any(v in low for v in _WRITE_VERBS):
            return True
        if "open(" in low and any(m in low for m in _WRITE_MODES):
            return True
    return False


def _missing_required_input(state: DebugState, data_dir: Optional[Path], code: str) -> Optional[str]:
    """Return the name of a genuinely-absent PROVIDED input, else None.

    Absent = a FileNotFoundError whose file is not on disk AND the code does not
    write it first. Returns None for create-then-read (code writes it) so we
    never short-circuit the legitimate produce-then-consume case."""
    is_fnf, path = looks_like_file_not_found(state)
    if not is_fnf:
        return None
    if path:
        name = Path(path).name
        if data_dir is not None and (data_dir / name).exists():
            return None  # the file IS there — a different fault, not "missing"
        if _code_writes(code, name):
            return None  # create-then-read — let the engineer produce it
        return name
    # No parseable path: only call it missing when data/ is empty AND the code
    # reads but never writes any file (a pure consumer of an absent input).
    if data_dir is not None and not any(data_dir.iterdir()):
        if not any(v in code.lower() for v in _WRITE_VERBS):
            return "(required input file)"
    return None


# ── deterministic file inspection ─────────────────────────────────────────

_MAGIC = [
    (b"\x1f\x8b", "gzip-compressed"),
    (b"\x89HDF\r\n\x1a\n", "HDF5"),
    (b"\x93NUMPY", "npy array"),
    (b"PK\x03\x04", "zip container (npz / parquet / xlsx / zip)"),
    (b"BZh", "bzip2-compressed"),
]


def _inspect_one(p: Path) -> str:
    """A deterministic inspection report for one file — magic bytes, first
    lines, sniffed CSV delimiter, HDF5 tree, npy dtype. Every probe is guarded;
    a probe that fails is simply omitted."""
    out = [f"### {p.name} ({p.stat().st_size} bytes)"]
    try:
        raw = p.read_bytes()[:8192]
    except Exception as e:  # noqa: BLE001
        return "\n".join(out + [f"(could not read: {e})"])

    out.append(f"first bytes: {raw[:48]!r}")
    magic = next((desc for sig, desc in _MAGIC if raw.startswith(sig)), None)
    if raw[:1] in (b"{", b"[") and magic is None:
        magic = "JSON text"
    out.append(f"magic → {magic or 'plain text / delimited (no binary magic)'}")

    # gzip: peek at the decompressed head so the delimiter sniff sees real text.
    sample_text = None
    if raw.startswith(b"\x1f\x8b"):
        try:
            import gzip
            sample_text = gzip.decompress(p.read_bytes()).decode("utf-8", "replace")[:2000]
            out.append("decompressed head:\n" + "\n".join(sample_text.splitlines()[:5]))
        except Exception as e:  # noqa: BLE001
            out.append(f"(gzip decompress failed: {e})")
    elif raw.startswith(b"\x89HDF"):
        try:
            import h5py
            tree = []
            with h5py.File(p, "r") as f:
                def _visit(name, obj):
                    kind = "Dataset" if isinstance(obj, h5py.Dataset) else "Group"
                    shape = getattr(obj, "shape", None)
                    tree.append(f"  {name}  [{kind}{'' if shape is None else ' shape=' + str(shape)}]")
                f.visititems(_visit)
            out.append("HDF5 group/dataset tree:\n" + "\n".join(tree))
        except Exception as e:  # noqa: BLE001
            out.append(f"(h5py enumeration failed: {e})")
    elif raw.startswith(b"\x93NUMPY"):
        try:
            import numpy as np
            arr = np.load(p, allow_pickle=True)
            out.append(f"npy: dtype={arr.dtype} shape={arr.shape}")
            if arr.dtype == object:
                out.append("object/pickled array; "
                            "elements: " + repr(arr.reshape(-1)[:1]))
        except Exception as e:  # noqa: BLE001
            out.append(f"(np.load failed: {e})")
    else:
        # Plain/delimited text — decode and sniff.
        try:
            sample_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                sample_text = raw.decode("latin-1")
                out.append("encoding: not valid UTF-8; decodes as latin-1")
            except Exception:  # noqa: BLE001
                sample_text = None

    if sample_text:
        head_lines = sample_text.splitlines()[:5]
        out.append("first lines:\n" + "\n".join(repr(ln) for ln in head_lines))
        # comment preamble?
        n_comment = 0
        for ln in head_lines:
            if ln.lstrip().startswith("#"):
                n_comment += 1
            else:
                break
        if n_comment:
            out.append(f"comment preamble: {n_comment} leading '#' line(s) before the header")
        # delimiter sniff on the first non-comment line
        body = "\n".join(sample_text.splitlines()[n_comment:n_comment + 20])
        try:
            dialect = csv.Sniffer().sniff(body, delimiters=",;\t| ")
            out.append(f"csv.Sniffer → delimiter={dialect.delimiter!r}")
        except Exception:  # noqa: BLE001
            pass
        # decimal-comma / thousands hints
        first_data = next((ln for ln in sample_text.splitlines()[n_comment + 1:] if ln.strip()), "")
        if re.search(r"\d+,\d+", first_data) and ";" in first_data:
            out.append(f"numeric format: comma appears inside numbers, e.g. {first_data[:60]!r}")
        elif re.search(r"\"?\d{1,3}(,\d{3})+\"?", first_data):
            out.append(f"numeric format: comma appears at 3-digit intervals inside numbers, "
                        f"e.g. {first_data[:60]!r}")
    return "\n".join(out)


def _inspection_report(data_dir: Optional[Path], target: Optional[str]) -> str:
    """Inspect the target file if known, else every file in data/ (usually one)."""
    if data_dir is None:
        return "(no work_dir/data — nothing to inspect)"
    files = sorted(p for p in data_dir.iterdir() if p.is_file())
    if target:
        tp = data_dir / Path(target).name
        if tp.exists():
            files = [tp]
    if not files:
        return "(data/ is empty — no input file present)"
    return "\n\n".join(_inspect_one(p) for p in files[:4])


# ── the node ──────────────────────────────────────────────────────────────


def _model(ctx: PlanContext):
    name = ctx.data_fixer_model or ctx.engineer_model or _DEFAULT_DATA_FIXER_MODEL
    return chat_model(name, "coder")


@timed_node("data_fixer")
def data_fixer(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Inspect the real input file and produce loader guidance for the next
    attempt — or, for an absent required input, refuse and terminate."""
    ctx = runtime.context
    n = state.get("step_number", 1)
    code = ""
    cc = state.get("current_code")
    if cc is not None:
        code = getattr(cc, "python_code", "") or ""
    data_dir = _data_dir(state)

    # Wrap EVERYTHING — the fixer must never crash the graph. On failure it marks
    # data_fixed=True (one-shot) with no guidance and falls back to retry.
    try:
        # (1) Deterministic missing-input adjudication FIRST — the dangerous case, decided
        #     with no model and no fabrication.
        missing = _missing_required_input(state, data_dir, code)
        if missing is not None:
            notes = (f"Required input '{missing}' is absent from data/ and the "
                     f"script does not create it. It cannot be loaded; the step "
                     f"is unfulfillable. Data must NOT be fabricated to stand in "
                     f"for it.")
            print(f"[data_fixer] MISSING input {missing!r} → terminal not-fulfilled "
                  f"(refusing to fabricate)", file=sys.stderr, flush=True)
            _log(state, n, {"format": "missing", "missing": missing, "notes": notes})
            # A clear terminal verdict so consumers see WHY it ended.
            terminal = StepVerdict(
                fulfilled=False,
                unmet_requirements=[f"required input '{missing}' is missing and cannot be loaded"],
                feedback=notes,
            )
            return {
                "data_fixed": True,
                "data_fixer_format": "missing",
                "data_fixer_guidance": notes,
                "current_step_verdict": terminal,
            }

        # (2) Present-but-mis-parsed: deterministic report → one Gemini call.
        _, target = looks_like_file_not_found(state)
        report = _inspection_report(data_dir, target)
        stderr = state.get("execution_stderr", "") or ""

        system = SystemMessage(
            "You are a data-loading specialist. A Python script failed while "
            "LOADING a provided input file. Below is a DETERMINISTIC inspection "
            "of the real file's bytes (magic number, first lines, sniffed CSV "
            "delimiter, HDF5 tree, npy dtype). Using ONLY this evidence, produce "
            "the correct loader so the next attempt reads the file right first "
            "try. Rules:\n"
            "- Choose the loader and every reader argument from the inspection "
            "evidence (magic bytes, delimiter, container keys, dtypes), never "
            "from the extension or the task wording.\n"
            "- Never mask a bad read by coercing or dropping rows — fix the "
            "reader arguments instead. In particular do NOT 'fix' bad values "
            "with to_numeric(coerce)+dropna (that silently deletes rows).\n"
            "- Select the target column/array by its real name and assert it is "
            "numeric and finite.\n"
            "- If the evidence shows the file is absent or empty, set "
            "format='missing' and invent nothing.\n"
        )
        user = HumanMessage(
            f"----- SUB-TASK -----\n{state['step'].sub_task}\n\n"
            f"----- FILE INSPECTION -----\n{report}\n\n"
            f"----- HOW THE LOAD FAILED (stderr, truncated) -----\n{stderr[:1500]}\n\n"
            f"----- THE FAILING SCRIPT (truncated) -----\n{code[:2500]}\n\n"
            "Emit a DataLoaderFix. Cover these fields:\n\n"
            + schema_field_brief(DataLoaderFix)
        )
        structured = _model(ctx).with_structured_output(DataLoaderFix)
        fix: DataLoaderFix = structured.invoke([system, user], config={"tags": ["data_fixer"]})

        if fix.format == "missing":
            # The model, from the bytes, judges the file unloadable/absent.
            notes = fix.notes or "the input file is absent or empty and cannot be loaded"
            print(f"[data_fixer] model reports format=missing → terminal", file=sys.stderr, flush=True)
            _log(state, n, {"format": "missing", "notes": notes, "source": "model"})
            terminal = StepVerdict(
                fulfilled=False,
                unmet_requirements=["the required input file cannot be loaded"],
                feedback=notes + " Data must NOT be fabricated to substitute for it.",
            )
            return {
                "data_fixed": True,
                "data_fixer_format": "missing",
                "data_fixer_guidance": notes,
                "current_step_verdict": terminal,
            }

        guidance = fix.format_guidance()
        print(f"[data_fixer] format={fix.format} → guidance handed to next engineer attempt",
              file=sys.stderr, flush=True)
        _log(state, n, {"format": fix.format, "guidance": guidance})
        return {
            "data_fixed": True,
            "data_fixer_format": fix.format,
            "data_fixer_guidance": guidance,
        }
    except Exception as e:  # noqa: BLE001 — non-fatal: fall back to normal retry
        print(f"[data_fixer] non-fatal error, falling back to retry: {e}",
              file=sys.stderr, flush=True)
        return {"data_fixed": True}


def _log(state: DebugState, n: int, record: dict) -> None:
    logs = _logs_dir(state)
    if logs is not None:
        try:
            (logs / f"step_{n}_data_fixer.json").write_text(json.dumps(record, indent=2))
        except Exception:  # noqa: BLE001
            pass


__all__ = ["data_fixer", "should_data_fix", "looks_like_data_load_error",
           "looks_like_file_not_found", "has_input_files"]
