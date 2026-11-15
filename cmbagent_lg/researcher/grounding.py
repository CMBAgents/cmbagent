"""Deterministic claim grounding for the researcher graders — no LLM, never raises.

Why this exists. Every grader window used to be an unmarked prefix of a prefix:
`read_file` stored the first 4,000 chars of what it returned, `_read_sources`
re-sliced that to 8,000 / 2,500, and the step summary capped stdout at
6,000 + 2,000. An LLM cannot prove that a number is ABSENT from an excerpt — but
it did: on the live fleet a report's R² values sat at byte 7,567 of a 11,996-byte
log, outside every window, and both graders ruled "not in the file → fabricated".
The researcher was told to delete true numbers eight times and the analysis was
halted.

Principle. Presence and absence of a number are made DETERMINISTIC findings —
a whole-token lookup over the run's full sources — and the LLM keeps authority
only over meaning (misattribution, contradiction, trends). Deterministic absence
may still fail a step; grader uncertainty alone never does.

Nothing here is domain-specific: identifiers come from the claim itself, the
formats are CSV/JSON/pandas/log lines, and the coincidence threshold is derived
from the run's own number density.

Public surface: `SourceIndex.build`, `extract_claim_numbers`, `ground`,
`render_ledger`, `render_evidence`, `apply_overrides`, `fingerprint`.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Literal, Optional

Tier = Literal["primary", "figure", "meta", "superseded", "input", "excluded"]

# ── knobs ────────────────────────────────────────────────────────────────

_DEFAULTS = {
    "grounding_enabled": True,
    "grounding_budget_s": 1.5,
    "grounding_file_head_bytes": 16 * 1024 * 1024,
    "grounding_file_tail_bytes": 4 * 1024 * 1024,
    "grounding_coincidence_max": 0.05,
    "evidence_ledger_chars": 24_000,
    "researcher_read_page_chars": 8_000,
    "claim_audit_halts": True,
}


def knob(ctx, name: str, default=None):
    """A grounding knob: the context field, else `CMBAGENT_LG_<NAME>` from the
    environment, else the module default. The env fallback exists so a fleet can
    flip a knob on a deployed worker without a code change; the type is coerced
    from the default so `"0"` disables a boolean knob."""
    if default is None:
        default = _DEFAULTS.get(name)
    val = getattr(ctx, name, None) if ctx is not None else None
    if val is None:
        raw = os.environ.get(f"CMBAGENT_LG_{name.upper()}")
        if raw is not None and raw != "":
            try:
                if isinstance(default, bool):
                    return raw.strip().lower() not in ("0", "false", "no", "off")
                if isinstance(default, int):
                    return int(float(raw))
                if isinstance(default, float):
                    return float(raw)
                return raw
            except ValueError:
                return default
        return default
    return val


# ── data structures ──────────────────────────────────────────────────────


@dataclass
class NumToken:
    value: float
    text: str
    decimals: int
    sig: int
    source: str        # path relative to work_dir (or "figure:<path>", "json path")
    line: int          # 1-based line number, 0 when unknown
    tier: Tier
    label: str         # column header + row key / key path / the line ±1 words
    neighbour: str     # the line the number sits on (rendered in the ledger)
    meta: str = ""     # "" or why the token is context, not a value (seed/shape/…)
    pct: bool = False


@dataclass
class Coverage:
    files: dict = field(default_factory=dict)   # rel path → {bytes, indexed_bytes, truncated, tier}
    incomplete: bool = False
    elapsed_s: float = 0.0
    researcher_ranges: dict = field(default_factory=dict)  # rel path → [[a, b], …]

    @property
    def n_files(self) -> int:
        return sum(1 for f in self.files.values() if f.get("indexed_bytes"))

    @property
    def indexed_bytes(self) -> int:
        return sum(int(f.get("indexed_bytes") or 0) for f in self.files.values())

    def describe(self) -> str:
        mb = self.indexed_bytes / 1e6
        s = f"{self.n_files} files, {mb:.1f} MB scanned"
        if self.incomplete:
            s += " — INDEX INCOMPLETE (budget exhausted; absence is not proven)"
        return s

    def to_dict(self) -> dict:
        return {"files": self.files, "incomplete": self.incomplete,
                "elapsed_s": round(self.elapsed_s, 4),
                "researcher_ranges": self.researcher_ranges}


@dataclass
class ClaimNumber:
    text: str
    value: float
    decimals: int
    sig: int
    pct: bool
    kind: Literal["value", "approx", "bound", "range", "pm"]
    idents: List[str]
    cited_path: Optional[str]
    canonical: str = ""
    op: str = ""          # for bounds: "<", ">", "<=", ">="
    pos: int = 0          # char offset in the unwrapped statement

    @property
    def is_integer(self) -> bool:
        return self.decimals <= 0 and float(self.value).is_integer()

    def to_dict(self) -> dict:
        return {"text": self.text, "value": self.value, "decimals": self.decimals,
                "sig": self.sig, "pct": self.pct, "kind": self.kind,
                "idents": self.idents[:12], "cited_path": self.cited_path, "op": self.op}


@dataclass
class Hit:
    token: NumToken
    match: Literal["exact", "rounded", "sci", "percent", "approx", "thousands"]
    strength: Literal["strong", "weak"]
    reason: str

    def to_dict(self) -> dict:
        t = self.token
        return {"value": t.text, "source": t.source, "line": t.line, "tier": t.tier,
                "label": t.label[:160], "neighbour": t.neighbour[:200],
                "match": self.match, "strength": self.strength, "reason": self.reason}


@dataclass
class QuantityGrounding:
    number: ClaimNumber
    hits: List[Hit]
    best: Literal["strong", "weak", "none"]
    input_only: List[NumToken] = field(default_factory=list)
    note: str = ""        # e.g. "found only in a superseded failure log"

    def to_dict(self) -> dict:
        return {"number": self.number.to_dict(), "best": self.best,
                "hits": [h.to_dict() for h in self.hits[:12]],
                "input_only": [{"source": t.source, "line": t.line, "value": t.text}
                               for t in self.input_only[:6]],
                "note": self.note}


@dataclass
class ClaimGrounding:
    statement: str
    state: Literal["grounded", "weakly_grounded", "partial", "ungrounded",
                   "bounds_only", "no_quantities"]
    quantities: List[QuantityGrounding]
    cited_and_verified: bool
    wrong_citation: bool
    fingerprint: str
    bound_checks: List[str] = field(default_factory=list)

    @property
    def rescuable(self) -> bool:
        """Grounding says the numbers exist in this run's own outputs."""
        return (self.state in ("grounded", "weakly_grounded", "bounds_only")
                or self.cited_and_verified)

    @property
    def deterministically_absent(self) -> bool:
        return self.state in ("ungrounded", "partial")

    def to_dict(self) -> dict:
        return {"statement": self.statement, "state": self.state,
                "cited_and_verified": self.cited_and_verified,
                "wrong_citation": self.wrong_citation, "fingerprint": self.fingerprint,
                "quantities": [q.to_dict() for q in self.quantities],
                "bound_checks": self.bound_checks}


# ── tokeniser ────────────────────────────────────────────────────────────

# Whole-number tokens only: no leading word char / dot / caret (so `3054` never
# comes out of `4.783054`, `2` never out of `R^2`, `12` never out of `step_12`),
# and no trailing `.digit` (so `1.2` never comes out of `1.2.3`). Thousands
# groups are allowed in prose/logs, NOT in CSV/JSON where `1,234` is two cells.
_NUM_PROSE = re.compile(
    r"(?<![\w.^])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![\w.]?\d)")
_NUM_PLAIN = re.compile(
    r"(?<![\w.^])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![\w.]?\d)")
_HAS_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

_STOPWORDS = frozenset("""
a an and are as at be been being between both but by can could did do does
each either for from had has have how if in into is it its itself more most
much must no nor not of on or other our out over own per same she he so some
such than that the their them then there these they this those through to too
under up very was we were what when where which while who whom why will with
would you your about above after again against all also am any because before
below down during few further here just once only off should until
report reports result results value values shows show showed shown observed
respectively approximately approx around roughly nearly about measured
indicating indicates indicated suggests suggesting suggest confirm confirms
across within whereas while versus vs consistently significantly typically
""".split())

_APPROX_WORDS = re.compile(
    r"(?:~|≈|\babout\b|\bapproximately\b|\bapprox\.?\b|\baround\b|\broughly\b|"
    r"\bcirca\b|\bnearly\b|\bclose to\b|\bnear\b)\s*$", re.I)
_BOUND_SYM = re.compile(r"([<>≤≥]=?)\s*$")
_BOUND_WORDS = re.compile(
    r"\b(less than|greater than|more than|fewer than|at least|at most|below|above|"
    r"under|no more than|no less than|up to|smaller than|larger than)\s*$", re.I)
_N_EQ = re.compile(r"\b[nN]\s*=\s*$")
_PCT_AFTER = re.compile(r"^\s*(?:%|percent\b|per cent\b)")
_RANGE_BETWEEN = re.compile(
    r"\bbetween\s+(?:approximately\s+|about\s+|roughly\s+|~\s*)?(NUM)\s+and\s+(?:approximately\s+|about\s+|~\s*)?(NUM)"
    .replace("NUM", _NUM_PROSE.pattern), re.I)
_CITED = re.compile(
    r"((?:[\w.-]+/)*[\w.-]+\.(?:log|csv|tsv|json|txt|md|png|jpg|jpeg|npz|npy|pkl|h5|hdf5|parquet))\b")

# Line contexts whose numbers are bookkeeping, not results.
_META_LINE = re.compile(
    r"(?:\bseed\b|random_state|random_seed|\bTraceback \(most recent|^\s*File \"|"
    r"^\s*at line \d|\bline \d+\b|\bversion\b|\bpid\b|\btimestamp\b)", re.I)
_META_SPANS = [
    ("timestamp", re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b")),
    ("shape", re.compile(r"\(\s*\d+\s*(?:,\s*\d+\s*)+\)|\bshape\s*[:=]?\s*\[?\(?\s*\d+(?:\s*,\s*\d+)*")),
]
_ELAPSED_AFTER = re.compile(r"^\s*(?:s|sec|secs|seconds?|ms|min|mins|minutes?|hours?|h)\b")
_ELAPSED_BEFORE = re.compile(r"(?:elapsed|took|runtime|wall[- ]?time|duration|overhead of testing was)\W*$", re.I)
_ELAPSED_LINE = re.compile(r"elapsed|took|runtime|wall|duration|overhead|\d\s*(?:s|sec|ms|min|h)\b", re.I)
# One cheap pre-filter per line: only a line that can possibly carry a meta
# context (seed, traceback, timestamp, shape, timing, version…) pays for the
# four precise regexes above. On a dense numeric log this is most of the cost.
_META_HINT = re.compile(
    r"[Ss]eed|random_|Traceback|File \"|line \d|[Vv]ersion|\bpid\b|timestamp|"
    r"[Ee]lapsed|took|runtime|[Ww]all|duration|overhead|\d{4}-\d{2}-\d{2}|\d:\d\d|"
    r"\(\s*\d+\s*,|[Ss]hape|\d\s*(?:s|sec|secs|seconds?|ms|min|mins|minutes?|hours?|h)\b")

_TEXT_EXTS = {".csv", ".tsv", ".json", ".txt", ".md", ".log"}
_DATA_MAX_BYTES = 2 * 1024 * 1024
_MAX_LINE_KEEP = 400  # chars of a line kept for neighbour/label rendering


def _parse_num(text: str):
    """(value, decimals, sig, pct) for a matched token, or None."""
    pct = text.endswith("%")
    core = text.rstrip("%").replace(",", "")
    try:
        value = float(core)
    except ValueError:
        return None
    if math.isinf(value) or math.isnan(value):
        return None
    mant, _, exp = core.lower().partition("e")
    try:
        e = int(exp) if exp else 0
    except ValueError:
        e = 0
    ip, _, fp = mant.lstrip("+-").partition(".")
    decimals = len(fp) - e
    digits = (ip + fp).lstrip("0")
    if not digits:
        sig = 1
    elif fp:
        sig = len(digits)
    else:
        sig = len(digits.rstrip("0")) or 1  # trailing integer zeros: not significant
    return value, decimals, sig, pct


def canonical(text: str) -> str:
    """Digits-only identity: separators and `%` stripped, `+` dropped, leading
    integer zeros and trailing fraction zeros removed, `-0.00` ≡ `0.00`, sci
    notation expanded. Two tokens are `exact` matches iff their canonicals agree."""
    core = text.strip().rstrip("%").replace(",", "").replace("+", "")
    if "e" in core.lower():
        try:
            from decimal import Decimal
            core = format(Decimal(core), "f")
        except Exception:  # noqa: BLE001
            pass
    neg = core.startswith("-")
    core = core.lstrip("-")
    ip, dot, fp = core.partition(".")
    ip = ip.lstrip("0") or "0"
    fp = fp.rstrip("0")
    s = ip + ("." + fp if fp else "")
    if s == "0":
        return "0"
    return ("-" if neg else "") + s


def _exp10(v: float) -> int:
    return int(math.floor(math.log10(abs(v)))) if v else 0


def _words(s: str) -> List[str]:
    """Identifier words of a string, lowercased, with `_`-split parts added so a
    column `res_zpve` also answers to `zpve`. Pure digits are not words."""
    out: List[str] = []
    for w in _WORD.findall(s):
        lw = w.lower()
        out.append(lw)
        if "_" in lw:
            out.extend(p for p in lw.split("_") if len(p) >= 2 and not p.isdigit())
    return out


# ── the index ────────────────────────────────────────────────────────────


@dataclass
class _File:
    rel: str
    tier: Tier
    kind: str            # log | csv | json | text | figure
    lines: List[str]
    stderr_from: int = -1      # line index where `--- stderr ---` starts (logs)
    header: Optional[List[str]] = None  # csv header cells


class SourceIndex:
    """Whole-token number index over the run's own artifacts.

    Built once per step (`SourceIndex.for_step`) and shared by the evaluator, the
    auditor and the override; `tokens_by_exp` / `n_bucket` are the spec'd views,
    the compact parallel lists underneath keep a 20 MB log inside the budget.
    """

    def __init__(self, work_dir: Optional[Path]):
        self.work_dir = work_dir
        self.coverage = Coverage()
        self._files: List[_File] = []
        self._val: List[float] = []
        self._txt: List[str] = []
        self._fid: List[int] = []
        self._ln: List[int] = []
        self._meta: List[str] = []
        self._col: List[int] = []      # csv column / table field index, -1 otherwise
        self._path: List[str] = []     # json key path, "" otherwise
        self._pos: List[int] = []      # char offset within the line, -1 otherwise
        self._buckets: dict = {}       # exp → [(value, tid)] (sorted lazily)
        self._sorted: set = set()
        self._nb: Optional[dict] = None
        self.key: str = ""

    # ── construction ────────────────────────────────────────────────────

    @classmethod
    def build(cls, work_dir, step_number: int = 1, *, budget_s: float = 1.5,
              head_bytes: int = 16 * 1024 * 1024, tail_bytes: int = 4 * 1024 * 1024,
              ctx=None) -> "SourceIndex":
        """Index this run's sources from disk. Never raises: an unreadable file
        is recorded in `coverage` and skipped; exceeding `budget_s` marks the
        coverage incomplete (absence is then not proven and never charged)."""
        t0 = time.monotonic()
        try:
            wd = Path(work_dir).expanduser() if work_dir else None
        except Exception:  # noqa: BLE001
            wd = None
        idx = cls(wd)
        if ctx is not None:
            budget_s = float(knob(ctx, "grounding_budget_s", budget_s))
            head_bytes = int(knob(ctx, "grounding_file_head_bytes", head_bytes))
            tail_bytes = int(knob(ctx, "grounding_file_tail_bytes", tail_bytes))
        try:
            if wd is not None and wd.is_dir():
                idx._build(wd, int(step_number or 1), t0, budget_s, head_bytes, tail_bytes)
        except Exception as e:  # noqa: BLE001 — grounding must never break a run
            idx.coverage.incomplete = True
            print(f"[grounding] index build error ({e!r}) → coverage incomplete",
                  file=sys.stderr, flush=True)
        idx.coverage.elapsed_s = time.monotonic() - t0
        return idx

    @staticmethod
    def source_set(wd: Path, step_number: int) -> List[tuple]:
        """(path, tier, kind) for every file this run's grounding may cite.

        Primary: `codebase/step_*.log` stdout and text files under `data/`.
        Superseded: failure/bounced logs. Input: `input_files/*` (beside or
        inside the work_dir — Denario keeps the inputs next to the experiment
        output) and `planning/`. Figure: the researcher's own `view_image`
        readings for this step. Never: `*.py`, `reports/`, verdict/audit JSON.
        """
        out: List[tuple] = []
        cb = wd / "codebase"
        if cb.is_dir():
            for p in sorted(cb.iterdir()):
                if p.suffix != ".log" or not p.is_file():
                    continue
                tier = "superseded" if ("_failure_" in p.name or "_bounced_" in p.name) else "primary"
                out.append((p, tier, "log"))
        data = wd / "data"
        if data.is_dir():
            for p in sorted(data.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in _TEXT_EXTS:
                    continue
                kind = {"csv": "csv", "tsv": "csv", "json": "json"}.get(p.suffix.lower()[1:], "text")
                out.append((p, "primary", kind))
        logs = wd / "logs"
        if logs.is_dir():
            for p in sorted(logs.glob(f"step_{step_number}_researcher_tools_attempt*.json")):
                out.append((p, "figure", "figure"))
        for base in (wd, wd.parent):
            inp = base / "input_files"
            if inp.is_dir():
                for p in sorted(inp.iterdir()):
                    if p.is_file() and p.suffix.lower() in (".md", ".txt", ".csv", ".json", ".tsv"):
                        out.append((p, "input", "text" if p.suffix.lower() in (".md", ".txt") else
                                    ("csv" if p.suffix.lower() in (".csv", ".tsv") else "json")))
                break
        plan = wd / "planning"
        if plan.is_dir():
            for p in sorted(plan.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".md", ".txt", ".json"):
                    out.append((p, "input", "json" if p.suffix.lower() == ".json" else "text"))
        return out

    @staticmethod
    def digest(wd, step_number: int) -> str:
        """Cheap identity of the source set: paths, sizes and mtimes. Used to share
        one index across the evaluator, auditor and override of one attempt."""
        try:
            wd = Path(wd).expanduser()
            parts = []
            for p, tier, kind in SourceIndex.source_set(wd, step_number):
                st = p.stat()
                parts.append(f"{p}|{st.st_size}|{int(st.st_mtime)}")
            return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return ""

    def _rel(self, p: Path) -> str:
        if self.work_dir is None:
            return p.name
        try:
            return str(p.relative_to(self.work_dir))
        except Exception:  # noqa: BLE001
            try:
                return str(p.relative_to(self.work_dir.parent))
            except Exception:  # noqa: BLE001
                return p.name

    def _read(self, p: Path, head_bytes: int, tail_bytes: int, cap: Optional[int]):
        """Text of `p` with head/tail truncation; (text, nbytes, truncated) or None."""
        n = p.stat().st_size
        if cap is not None and n > cap:
            self.coverage.files[self._rel(p)] = {"bytes": n, "indexed_bytes": 0,
                                                 "truncated": True, "skipped": f"> {cap} bytes"}
            return None
        with open(p, "rb") as fh:
            if n <= head_bytes + tail_bytes:
                raw = fh.read()
                truncated = False
            else:
                head = fh.read(head_bytes)
                fh.seek(max(0, n - tail_bytes))
                raw = head + b"\n[... omitted by the grounding index ...]\n" + fh.read()
                truncated = True
        if b"\x00" in raw[:4096]:
            self.coverage.files[self._rel(p)] = {"bytes": n, "indexed_bytes": 0,
                                                 "truncated": True, "skipped": "binary"}
            return None
        text = raw.decode("utf-8", errors="replace")
        return text, n, truncated

    def _build(self, wd: Path, step_number: int, t0: float, budget_s: float,
               head_bytes: int, tail_bytes: int) -> None:
        for p, tier, kind in self.source_set(wd, step_number):
            if time.monotonic() - t0 > budget_s:
                self.coverage.incomplete = True
                self.coverage.files[self._rel(p)] = {"bytes": p.stat().st_size, "indexed_bytes": 0,
                                                     "truncated": True, "skipped": "budget"}
                continue
            try:
                cap = _DATA_MAX_BYTES if (tier in ("primary", "input") and kind != "log") else None
                got = self._read(p, head_bytes, tail_bytes, cap)
                if got is None:
                    continue
                text, n, truncated = got
                rel = self._rel(p)
                self.coverage.files[rel] = {"bytes": n, "indexed_bytes": len(text),
                                            "truncated": truncated, "tier": tier}
                if kind == "figure":
                    self._index_tools_log(rel, text, tier)
                elif kind == "csv":
                    self._index_csv(rel, text, tier, p.suffix.lower())
                elif kind == "json":
                    self._index_json(rel, text, tier)
                else:
                    self._index_lines(rel, text, tier, kind)
                if truncated:
                    self.coverage.incomplete = True
            except Exception as e:  # noqa: BLE001
                self.coverage.files[self._rel(p)] = {"bytes": 0, "indexed_bytes": 0,
                                                     "truncated": True, "skipped": repr(e)}
        if time.monotonic() - t0 > budget_s:
            self.coverage.incomplete = True

    def _add(self, fid: int, ln: int, text: str, meta: str, col: int = -1, path: str = "",
             pos: int = -1) -> int:
        """Record one token. Only the float is computed here (the hot loop); digits,
        decimals and labels are derived lazily for the few tokens a claim touches.
        Two tokens with the same digits parse to the same float, and a longer
        digit string parses to a different one, so value equality IS `exact`."""
        try:
            value = float(text.rstrip("%").replace(",", ""))
        except ValueError:
            return -1
        if value != value or value in (float("inf"), float("-inf")):
            return -1
        tid = len(self._val)
        self._val.append(value)
        self._txt.append(text)
        self._fid.append(fid)
        self._ln.append(ln)
        self._meta.append(meta)
        self._col.append(col)
        self._path.append(path)
        self._pos.append(pos)
        e = int(math.floor(math.log10(abs(value)))) if value else 0
        b = self._buckets.get(e)
        if b is None:
            self._buckets[e] = [(value, tid)]
        else:
            b.append((value, tid))
        return tid

    def _index_lines(self, rel: str, text: str, tier: Tier, kind: str) -> None:
        """Logs, text/markdown and figure readings: one token per whole number on
        each DISTINCT line (progress spam collapses), tagged meta by context.

        This is the hot loop — a 20 MB log is ~400k lines and ~1.5M tokens — so
        the token record is written inline with bound-method appends rather than
        through `_add`, and the four meta regexes only run on lines that pass
        `_META_HINT`. The record layout is exactly `_add`'s."""
        lines = text.split("\n")
        f = _File(rel, tier, kind, lines)
        fid = len(self._files)
        self._files.append(f)
        seen: set = set()
        in_stderr = False
        val_a, txt_a, fid_a, ln_a = self._val.append, self._txt.append, self._fid.append, self._ln.append
        meta_a, col_a, path_a, pos_a = self._meta.append, self._col.append, self._path.append, self._pos.append
        buckets = self._buckets
        finditer = _NUM_PROSE.finditer
        has_digit = _HAS_DIGIT.search
        hint = _META_HINT.search
        log10, floor = math.log10, math.floor
        inf = float("inf")
        n = len(self._val)
        for i, line in enumerate(lines):
            if kind == "log":
                if i == 0 and line.startswith("# returncode="):
                    continue  # the executor header: meta by definition
                if line.startswith("--- stderr ---"):
                    in_stderr = True
                    f.stderr_from = i
                    continue
                if line.startswith("--- stdout ---"):
                    continue
            if not has_digit(line):
                continue
            key = line.strip()
            if key in seen:
                continue
            seen.add(key)
            line_meta = "stderr" if in_stderr else ""
            spans = ()
            timed = False
            if not line_meta and hint(line):
                if _META_LINE.search(line):
                    line_meta = "meta"
                else:
                    spans = [(m.start(), m.end(), name) for name, rx in _META_SPANS for m in rx.finditer(line)]
                    timed = bool(_ELAPSED_LINE.search(line))
            ln = i + 1
            for m in finditer(line):
                tok = m.group(0)
                try:
                    # `%` and thousands separators are part of the token text
                    # (the ledger quotes them) but not of its value.
                    value = float(tok.rstrip("%").replace(",", ""))
                except ValueError:
                    continue
                if value == inf or value == -inf or value != value:
                    continue
                meta = line_meta
                if not meta and (spans or timed):
                    st, en = m.start(), m.end()
                    for a, b, name in spans:
                        if st >= a and en <= b:
                            meta = name
                            break
                    if not meta and timed and (_ELAPSED_AFTER.match(line[en:en + 12]) or
                                               _ELAPSED_BEFORE.search(line[max(0, st - 40):st])):
                        meta = "elapsed"
                val_a(value); txt_a(tok); fid_a(fid); ln_a(ln); meta_a(meta)
                col_a(-1); path_a(""); pos_a(m.start())
                e = floor(log10(value if value > 0 else -value)) if value else 0
                b = buckets.get(e)
                if b is None:
                    buckets[e] = [(value, n)]
                else:
                    b.append((value, n))
                n += 1

    def _index_csv(self, rel: str, text: str, tier: Tier, suffix: str) -> None:
        lines = text.split("\n")
        f = _File(rel, tier, "csv", lines)
        fid = len(self._files)
        self._files.append(f)
        delim = "\t" if suffix == ".tsv" else ","
        first = next((l for l in lines if l.strip()), "")
        if delim == "," and "," not in first and ("\t" in first or ";" in first):
            delim = "\t" if "\t" in first else ";"
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        header: Optional[List[str]] = None
        seen: set = set()
        # csv.reader hides line numbers across quoted newlines; rows are single
        # lines in practice, so count rows as lines.
        for i, row in enumerate(reader):
            if not row:
                continue
            if header is None:
                header = [c.strip() for c in row]
                f.header = header
                continue
            key = delim.join(row)
            if key in seen:
                continue
            seen.add(key)
            for j, cell in enumerate(row):
                c = cell.strip()
                if not c or not _HAS_DIGIT.search(c):
                    continue
                if j == 0 and header and (header[0] == "" or header[0].lower() == "index"
                                          or header[0].startswith("Unnamed")):
                    continue
                m = _NUM_PLAIN.fullmatch(c)
                if m:
                    self._add(fid, i + 1, c, "", col=j)

    def _index_json(self, rel: str, text: str, tier: Tier) -> None:
        class _Raw(str):
            pass
        try:
            obj = json.loads(text, parse_float=_Raw, parse_int=_Raw)
        except Exception:  # noqa: BLE001 — not JSON after all: index as text
            self._index_lines(rel, text, tier, "text")
            return
        lines = text.split("\n")
        f = _File(rel, tier, "json", lines)
        fid = len(self._files)
        self._files.append(f)
        cursor = [0, 1]  # (char position, line number) — the walk order is the file order

        def locate(raw: str) -> int:
            pos = text.find(raw, cursor[0])
            if pos < 0:
                return 0
            cursor[1] += text.count("\n", cursor[0], pos)
            cursor[0] = pos + len(raw)
            return cursor[1]

        def walk(o, path: str):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, f"{path}.{k}" if path else str(k))
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    walk(v, f"{path}[{i}]")
            elif isinstance(o, _Raw):
                if _NUM_PLAIN.fullmatch(str(o)):
                    self._add(fid, locate(str(o)), str(o), "", path=path)
            elif isinstance(o, str):
                # A numeric string value ("0.9973") is still a number the run wrote.
                if _NUM_PLAIN.fullmatch(o.strip()):
                    self._add(fid, locate(o), o.strip(), "", path=path)
        walk(obj, "")

    def _index_tools_log(self, rel: str, text: str, tier: Tier) -> None:
        """Only the `view_image` readings: a `read_file` echo is a copy of a file
        indexed on its own, and must not double-count."""
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            return
        for k, s in enumerate(d.get("sources_read", []) or []):
            if s.get("tool") != "view_image":
                continue
            body = s.get("output") or ""
            name = f"figure:{Path(s.get('path', '?')).name}"
            self._index_lines(name, body, "figure", "figure")

    # ── views ───────────────────────────────────────────────────────────

    def token(self, tid: int) -> NumToken:
        f = self._files[self._fid[tid]]
        ln = self._ln[tid]
        label, neighbour = self._label(f, ln, tid)
        parsed = _parse_num(self._txt[tid]) or (0.0, 0, 1, False)
        tier = f.tier
        if self._meta[tid] == "stderr":
            tier = "meta"
        return NumToken(value=self._val[tid], text=self._txt[tid], decimals=parsed[1],
                        sig=parsed[2], source=f.rel, line=ln, tier=tier, label=label,
                        neighbour=neighbour, meta=self._meta[tid], pct=parsed[3])

    @property
    def n_bucket(self) -> dict:
        """Tokens per decade among the tiers that can ground (primary/figure, non-meta)."""
        if self._nb is None:
            nb: dict = {}
            for e, lst in self._buckets.items():
                nb[e] = sum(1 for _, tid in lst if self._grounds(tid))
            self._nb = nb
        return self._nb

    @property
    def tokens_by_exp(self) -> dict:
        return {e: [self.token(t) for _, t in self._sorted_bucket(e)] for e in self._buckets}

    @property
    def n_tokens(self) -> int:
        return len(self._val)

    def _grounds(self, tid: int) -> bool:
        return self._files[self._fid[tid]].tier in ("primary", "figure") and not self._meta[tid]

    def _sorted_bucket(self, e: int) -> list:
        lst = self._buckets.get(e)
        if not lst:
            return []
        if e not in self._sorted:
            lst.sort(key=lambda t: t[0])
            self._sorted.add(e)
        return lst

    def near(self, v: float, tol: float) -> List[int]:
        """Token ids with |value − v| ≤ tol."""
        lo, hi = v - tol, v + tol
        if lo <= 0 <= hi:
            # The interval spans zero: every smaller decade is inside it.
            top = max(_exp10(lo), _exp10(hi))
            exps = [e for e in self._buckets if e <= top]
        else:
            a, b = sorted((_exp10(lo), _exp10(hi)))
            exps = [e for e in self._buckets if a <= e <= b]
        out: List[int] = []
        for e in sorted(exps):
            lst = self._sorted_bucket(e)
            if not lst:
                continue
            vals = [t[0] for t in lst]
            i = bisect.bisect_left(vals, lo)
            while i < len(lst) and lst[i][0] <= hi:
                out.append(lst[i][1])
                i += 1
        return out

    def exact(self, value: float) -> List[int]:
        """Tokens whose digits equal the claim's (same float ⇔ same digits)."""
        return self.near(value, 0.0)

    def file_id(self, cited: Optional[str]) -> Optional[int]:
        """Resolve a path named in a claim to an indexed file (suffix match)."""
        if not cited:
            return None
        c = cited.strip().lstrip("./")
        for i, f in enumerate(self._files):
            if f.rel == c or f.rel.endswith("/" + c) or Path(f.rel).name == Path(c).name:
                return i
        return None

    def file_rel(self, fid: int) -> str:
        return self._files[fid].rel

    def file_lines(self, rel: str) -> Optional[List[str]]:
        for f in self._files:
            if f.rel == rel:
                return f.lines
        return None

    # ── labels ──────────────────────────────────────────────────────────

    def _label(self, f: _File, ln: int, tid: int):
        if f.kind == "csv":
            col = self._col[tid]
            row = f.lines[ln - 1] if 0 < ln <= len(f.lines) else ""
            delim = "\t" if "\t" in row else ("," if "," in row else ";")
            cells = [c.strip() for c in row.split(delim)]
            # The row key: its first cell unless that column is a pandas index
            # (`index` / `Unnamed` / blank header), plus the first non-numeric
            # cell — the family / name / label a claim would use to point here.
            keys = []
            if cells and f.header and not (f.header[0] == "" or f.header[0].lower() == "index"
                                           or f.header[0].startswith("Unnamed")):
                keys.append(cells[0])
            keys += [c for c in cells[:6] if c and not _NUM_PLAIN.fullmatch(c) and c not in keys][:2]
            colname = f.header[col] if f.header and 0 <= col < len(f.header) else ""
            return " ".join([colname] + keys).strip(), row[:_MAX_LINE_KEEP]
        if f.kind == "json":
            row = f.lines[ln - 1] if 0 < ln <= len(f.lines) else ""
            return self._path[tid], (row.strip() or self._path[tid])[:_MAX_LINE_KEEP]
        # log / text / figure: the line ±1, plus a table column resolved by position
        i = ln - 1
        line = f.lines[i] if 0 <= i < len(f.lines) else ""
        prev = f.lines[i - 1] if i - 1 >= 0 else ""
        nxt = f.lines[i + 1] if i + 1 < len(f.lines) else ""
        label_parts = [line, prev, nxt]
        table = self._table_label(f, i, tid)
        if table:
            label_parts.insert(0, table)
        return " ".join(p.strip() for p in label_parts if p.strip())[:_MAX_LINE_KEEP * 2], \
            line.strip()[:_MAX_LINE_KEEP]

    def _table_label(self, f: _File, i: int, tid: int) -> str:
        """Whitespace-aligned table (pandas `to_string`, numpy prints): the nearest
        preceding number-free line with at least as many words as this row has
        numeric fields names the column by position from the right; the row's own
        words are its key. Only for rows that ARE table rows (≤ 2 words)."""
        line = f.lines[i]
        nums = [m for m in _NUM_PROSE.finditer(line)]
        words = [w for w in line.replace("|", " ").split() if not _NUM_PROSE.fullmatch(w)]
        if len(nums) < 1 or len(words) > 2:
            return ""
        j = next((k for k, m in enumerate(nums) if m.start() == self._pos[tid]), None)
        if j is None:
            return ""
        k = len(nums)
        for back in range(1, 60):
            h = i - back
            if h < 0:
                break
            hl = f.lines[h]
            if not hl.strip():
                break
            hw = hl.replace("|", " ").split()
            if any(_NUM_PROSE.fullmatch(w) for w in hw):
                continue
            if len(hw) >= k:
                colname = hw[len(hw) - k + j]
                return f"{colname} {' '.join(words)}"
        return ""


# ── shared per-step index cache ───────────────────────────────────────────

_CACHE: dict = {}
_CACHE_MAX = 4


def index_for_step(work_dir, step_number: int, ctx=None) -> SourceIndex:
    """The step's `SourceIndex`, built once per (work_dir, step, source digest)
    and shared by the evaluator, the auditor and the override. `index.key` is
    what `ResearcherState["source_index_key"]` records."""
    key = f"{work_dir}|{step_number}|{SourceIndex.digest(work_dir, step_number)}"
    idx = _CACHE.get(key)
    if idx is None:
        idx = SourceIndex.build(work_dir, step_number, ctx=ctx)
        idx.key = key
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = idx
    return idx


# ── claim-side extraction ─────────────────────────────────────────────────

_UNWRAP = [
    (re.compile(r"\\_"), "_"),
    (re.compile(r"\*\*|__"), ""),
    (re.compile(r"\\approx|\\sim(?:eq)?"), "≈"),
    (re.compile(r"\\pm"), "±"),
    (re.compile(r"\\leq?|\\le\b"), "≤"),
    (re.compile(r"\\geq?|\\ge\b"), "≥"),
    (re.compile(r"\\times|\\cdot"), "×"),
    (re.compile(r"\\(?:mathrm|mathbf|text|textbf|textit|mathit|operatorname)\{([^}]*)\}"), r"\1"),
    (re.compile(r"\bR\^\{?2\}?|R²"), "r2"),
    (re.compile(r"([A-Za-z])\^\{?2\}?"), r"\g<1>2"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[×x*]\s*10\^\{?([-+]?\d+)\}?"), r"\1e\2"),
    (re.compile(r"\b10\^\{?([-+]?\d+)\}?"), r"1e\1"),
    (re.compile(r"\\([A-Za-z]+)"), r"\1"),
    (re.compile(r"[${}]"), " "),
    (re.compile(r"[−]"), "-"),
    (re.compile(r"`"), " "),
]


def unwrap(statement: str) -> str:
    s = statement or ""
    for rx, rep in _UNWRAP:
        s = rx.sub(rep, s)
    return s


def _idents_for(text: str, pos: int) -> List[str]:
    """Identifier words of the atomic claim, nearest to `pos` first."""
    seen = set()
    cands = []
    for m in _WORD.finditer(text):
        for w in _words(m.group(0)):
            if len(w) < 2 or w in _STOPWORDS or w.isdigit():
                continue
            if w in seen:
                continue
            seen.add(w)
            cands.append((abs(m.start() - pos), w))
    cands.sort()
    return [w for _, w in cands]


def extract_claim_numbers(statement: str) -> List[ClaimNumber]:
    """The quantities a statement asserts. LaTeX/markdown unwrapped; kind from
    context; ordinals (integers < 10 without `N=`/`%`) dropped; every number
    carries the claim's identifier words (nearest first) and any cited path."""
    try:
        raw = statement or ""
        text = unwrap(raw)
        cited = None
        m = _CITED.search(unwrap(raw))
        if m:
            cited = m.group(1)
        # range / pm structure first, so the members inherit their kind
        kinds: dict = {}
        for rm in _RANGE_BETWEEN.finditer(text):
            approx = bool(re.search(r"approximately|about|roughly|~", rm.group(0), re.I))
            for g in (1, 2):
                kinds[rm.start(g)] = "approx" if approx else "range"
        for rm in re.finditer(r"(NUM)\s*(?:±|\+/-|\+-)\s*(NUM)".replace("NUM", _NUM_PROSE.pattern), text):
            kinds[rm.start(1)] = "pm"
            kinds[rm.start(2)] = "pm"
        for rm in re.finditer(r"(NUM)\s*[–-]\s*(NUM)".replace("NUM", _NUM_PROSE.pattern), text):
            # `0.22–0.85` is a range only when the second number has no sign of
            # its own; `5 - 3` with spaces reads as arithmetic and is left alone.
            if rm.start(2) not in kinds:
                kinds.setdefault(rm.start(1), "range")
                kinds.setdefault(rm.start(2), "range")
        out: List[ClaimNumber] = []
        for m in _NUM_PROSE.finditer(text):
            tok = m.group(0)
            parsed = _parse_num(tok)
            if parsed is None:
                continue
            value, decimals, sig, pct = parsed
            before = text[max(0, m.start() - 24):m.start()]
            after = text[m.end():m.end() + 16]
            if not pct and _PCT_AFTER.match(after):
                pct = True
            kind = kinds.get(m.start(), "value")
            op = ""
            bm = _BOUND_SYM.search(before)
            if bm:
                kind, op = "bound", bm.group(1).replace("≤", "<=").replace("≥", ">=")
            else:
                bw = _BOUND_WORDS.search(before)
                if bw and kind == "value":
                    w = bw.group(1).lower()
                    kind = "bound"
                    op = ">" if w in ("greater than", "more than", "at least", "above", "larger than", "no less than") else "<"
                elif kind == "value" and _APPROX_WORDS.search(before):
                    kind = "approx"
            is_int = "." not in tok and "e" not in tok.lower()
            if is_int and abs(value) < 10 and not pct and kind != "bound" and not _N_EQ.search(before):
                continue  # ordinal / list index / "Mode 3": not a measured quantity
            out.append(ClaimNumber(
                text=tok, value=value, decimals=decimals, sig=sig,
                pct=pct, kind=kind, idents=_idents_for(text, m.start()), cited_path=cited,
                canonical=canonical(tok), op=op, pos=m.start()))
        return out
    except Exception:  # noqa: BLE001
        return []


# ── matching ──────────────────────────────────────────────────────────────


def _labelled(idents: Iterable[str], label: str) -> Optional[str]:
    lw = set(_words(label))
    for w in idents:
        if w in lw:
            return w
    return None


def _label_rank(ident: Optional[str], label: str) -> int:
    """How tightly the matched identifier sits: its position among the label's
    words. A log label is `table column+row | own line | prev | next`, so a
    column/row match ranks before a word borrowed from a neighbouring line —
    this is what tells the `p_value` of one row from the `dcor` of the row
    above when both hold the same digits."""
    if not ident:
        return 10**6
    try:
        return _words(label).index(ident)
    except ValueError:
        return 10**6


def ground_number(cn: ClaimNumber, index: SourceIndex, *, cited_fid: Optional[int] = None,
                  coincidence_max: float = 0.05) -> QuantityGrounding:
    """Every whole-token occurrence of one claim number in the index, graded."""
    try:
        return _ground_number(cn, index, cited_fid, coincidence_max)
    except Exception as e:  # noqa: BLE001
        return QuantityGrounding(cn, [], "none", note=f"grounding error: {e!r}")


def _ground_number(cn, index, cited_fid, coincidence_max) -> QuantityGrounding:
    if cn.kind == "bound":
        return QuantityGrounding(cn, [], "none")
    cands: dict = {}  # tid → match kind
    for tid in index.exact(cn.value):
        cands[tid] = "thousands" if "," in cn.text else "exact"
    tol = 0.5 * 10.0 ** (-cn.decimals) * (1 + 1e-9)
    if cn.kind == "approx":
        tol = 10.0 ** (-cn.decimals) * (1 + 1e-9)
    is_sci = "e" in cn.text.lower()
    for tid in index.near(cn.value, tol):
        if tid in cands:
            continue
        p = _parse_num(index._txt[tid])
        if p is None:
            continue
        if p[1] < cn.decimals:
            continue  # the source is coarser than the claim: the claim adds digits
        cands[tid] = "sci" if is_sci else ("approx" if cn.kind == "approx" else "rounded")
    # percent ↔ fraction (labelled only; enforced below)
    pct_cands: dict = {}
    if cn.pct:
        d2 = cn.decimals + 2
        frac = cn.value / 100.0
        for tid in index.near(frac, 0.5 * 10.0 ** (-d2) * (1 + 1e-9)):
            if tid in cands:
                continue
            p = _parse_num(index._txt[tid])
            if p is None or p[3] or p[1] < d2:
                continue  # an integer `1` never grounds `100%`
            pct_cands[tid] = "percent"
    else:
        for tid in index.near(cn.value * 100.0, 0.5 * 10.0 ** (-(cn.decimals - 2)) * (1 + 1e-9)):
            if tid in cands:
                continue
            p = _parse_num(index._txt[tid])
            if p is None or not p[3] or p[1] < cn.decimals - 2:
                continue
            pct_cands[tid] = "percent"
    hits: List[Hit] = []
    ranks: dict = {}
    input_only: List[NumToken] = []
    superseded = 0
    meta_only = 0
    coincidence = 0
    e_seen = 0.0
    for tid, kind in list(cands.items()) + list(pct_cands.items()):
        tok = index.token(tid)
        if tok.tier == "input":
            input_only.append(tok)
            continue
        if tok.tier in ("superseded", "excluded"):
            superseded += 1
            continue
        if tok.meta and not (tok.meta == "shape" and cn.is_integer):
            meta_only += 1
            continue
        if tok.tier == "meta":
            meta_only += 1
            continue
        ident = _labelled(cn.idents, tok.label)
        if kind == "percent" and not ident:
            continue  # a percent/fraction swap is only credible next to its name
        e = index.n_bucket.get(_exp10(tok.value), 0) * 10.0 ** (-cn.sig)
        reasons = []
        if ident:
            reasons.append(f"labelled ({ident})")
        if e < coincidence_max:
            reasons.append(f"specific (E={e:.3g})")
        if cited_fid is not None and index.file_rel(cited_fid) == tok.source:
            reasons.append("cited file")
        strength = "strong" if reasons else "weak"
        if not reasons:
            if e >= 1.0:
                # A match is EXPECTED by chance at this precision in this run (E
                # coincidences on average): it is not evidence of anything.
                coincidence += 1
                e_seen = e
                continue
            reasons.append(f"unlabelled, E={e:.3g}")
        hits.append(Hit(tok, kind, strength, "; ".join(reasons)))
        ranks[id(hits[-1])] = _label_rank(ident, tok.label)
    order = {"exact": 0, "thousands": 0, "rounded": 1, "sci": 1, "approx": 2, "percent": 3}
    cited_rel = index.file_rel(cited_fid) if cited_fid is not None else None
    hits.sort(key=lambda h: (h.strength != "strong", h.token.source != cited_rel,
                             "labelled" not in h.reason, ranks.get(id(h), 10**6),
                             order.get(h.match, 9), h.token.source, h.token.line))
    # ≤ 3 hits per file, ≤ 12 overall: the ledger is evidence, not a dump.
    per_file: dict = {}
    kept: List[Hit] = []
    for h in hits:
        c = per_file.get(h.token.source, 0)
        if c >= 3:
            continue
        per_file[h.token.source] = c + 1
        kept.append(h)
        if len(kept) >= 12:
            break
    best = "strong" if any(h.strength == "strong" for h in kept) else ("weak" if kept else "none")
    note = ""
    if not kept:
        if coincidence:
            note = (f"{coincidence} coincidence-level match(es) ignored: at this precision "
                    f"{e_seen:.3g} chance matches are expected in this run's sources")
        elif superseded:
            note = "found only in a superseded failure/bounced log"
        elif meta_only:
            note = "found only in bookkeeping context (seed / shape / timing / stderr)"
    return QuantityGrounding(cn, kept, best, input_only=input_only, note=note)


def _bound_check(cn: ClaimNumber, index: SourceIndex) -> str:
    """Evidence for a bound: labelled values in the primary tier checked against it."""
    try:
        if not cn.idents or not cn.op:
            return ""
        checked = 0
        viol = []
        e = _exp10(cn.value)
        for ee in (e - 1, e, e + 1, e + 2):
            for v, tid in index._sorted_bucket(ee):
                if not index._grounds(tid):
                    continue
                tok = index.token(tid)
                if not _labelled(cn.idents[:6], tok.label):
                    continue
                checked += 1
                ok = {"<": v < cn.value, "<=": v <= cn.value, ">": v > cn.value,
                      ">=": v >= cn.value}.get(cn.op, True)
                if not ok and len(viol) < 3:
                    viol.append(f"{tok.source}:{tok.line} {tok.neighbour[:80]!r}")
                if checked >= 400:
                    break
        if not checked:
            return f"bound {cn.op} {cn.text}: no labelled values to check"
        s = f"bound {cn.op} {cn.text}: {checked} labelled value(s) checked, {len(viol)} violate"
        if viol:
            s += " — " + "; ".join(viol)
        return s
    except Exception:  # noqa: BLE001
        return ""


def fingerprint(numbers: List[ClaimNumber], statement: str = "") -> str:
    """Identity of an accusation: the sorted canonical numbers plus cited paths, or
    the statement's words when there are no numbers. Stable across re-phrasings
    so a re-charge of the same claim is recognisable."""
    parts = sorted({n.canonical + ("%" if n.pct else "") for n in numbers})
    parts += sorted({n.cited_path for n in numbers if n.cited_path})
    if not parts:
        parts = sorted(set(_words(unwrap(statement))))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def ground(statement: str, index: SourceIndex, *, coincidence_max: float = 0.05) -> ClaimGrounding:
    """Ground one atomic claim against the index. Never raises."""
    try:
        numbers = extract_claim_numbers(statement)
        cited_fid = index.file_id(numbers[0].cited_path) if numbers else None
        qs = [ground_number(n, index, cited_fid=cited_fid, coincidence_max=coincidence_max)
              for n in numbers]
        values = [q for q in qs if q.number.kind != "bound"]
        bounds = [q for q in qs if q.number.kind == "bound"]
        cited_ok = wrong = False
        if cited_fid is not None and values:
            cited_rel = index.file_rel(cited_fid)
            in_cited = [any(h.token.source == cited_rel for h in q.hits) for q in values]
            if all(in_cited):
                cited_ok = True
                for q in values:
                    for h in q.hits:
                        if h.token.source == cited_rel and h.strength != "strong":
                            h.strength, h.reason = "strong", h.reason + "; cited file"
                    q.best = "strong"
            elif not any(in_cited) and all(q.best != "none" for q in values):
                wrong = True
        if not qs:
            state = "no_quantities"
        elif not values:
            state = "bounds_only"
        elif all(q.best == "strong" for q in values):
            state = "grounded"
        elif all(q.best != "none" for q in values):
            state = "weakly_grounded"
        elif any(q.best != "none" for q in values):
            state = "partial"
        else:
            state = "ungrounded"
        if wrong and state == "grounded":
            state = "weakly_grounded"
        checks = [c for c in (_bound_check(q.number, index) for q in bounds) if c]
        return ClaimGrounding(statement, state, qs, cited_ok, wrong,
                              fingerprint(numbers, statement), checks)
    except Exception as e:  # noqa: BLE001
        return ClaimGrounding(statement, "no_quantities", [], False, False,
                              fingerprint([], statement), [f"grounding error: {e!r}"])


# ── rendering ─────────────────────────────────────────────────────────────


def _fmt_hit(h: Hit) -> str:
    t = h.token
    where = f"{t.source}:{t.line}" if t.line else f"{t.source}@{t.label}"
    return f"{where} · {t.neighbour[:140]!r}"


def render_quantity(q: QuantityGrounding, coverage: Coverage) -> str:
    n = q.number
    shown = n.text
    if n.kind == "bound":
        return f"{n.op} {shown} — BOUND (a threshold, not a value; see bound check)"
    if q.best == "strong":
        h = q.hits[0]
        s = f"{shown} — GROUNDED {h.match} · {_fmt_hit(h)} [{h.reason}]"
    elif q.best == "weak":
        h = q.hits[0]
        s = f"{shown} — WEAK {h.match} · {_fmt_hit(h)} [{h.reason}]"
    elif q.input_only:
        t = q.input_only[0]
        s = f"{shown} — INPUT-ONLY ({t.source}:{t.line} — not computed by this run)"
    else:
        s = f"{shown} — NOT FOUND in any artifact ({coverage.describe()})"
        if q.note:
            s += f" — {q.note}"
    extra = [_fmt_hit(h) for h in q.hits[1:3]]
    if extra:
        s += "\n    also: " + " | ".join(extra)
    return s


def render_ledger(groundings: List[QuantityGrounding], coverage: Coverage,
                  max_chars: int = 24_000) -> str:
    """The QUANTITY LEDGER block: one line per report number."""
    if not groundings:
        return ""
    head = (
        "----- QUANTITY LEDGER (deterministic whole-token lookup over this run's FULL "
        "sources) -----\n"
        f"Scanned: {coverage.describe()}.\n"
        "GROUNDED / WEAK = the number exists in an artifact THIS run produced (the "
        "line it sits on is quoted so a coincidence is visible as one). INPUT-ONLY = "
        "it appears only in an input document, not in anything this run computed. "
        "NOT FOUND = a whole-token search of every artifact found nothing.\n"
        "A GROUNDED or WEAK number may NEVER be called unsupported_hard for absence: "
        "a hard verdict on it must be a contradiction or misattribution and must cite "
        "the conflicting ledger line. A NOT FOUND number is absent from this run's "
        "sources.\n\n"
    )
    lines = []
    for q in groundings:
        lines.append(render_quantity(q, coverage))
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n[… ledger cut at {max_chars} chars]"
    return head + body + "\n---------------------------------------------"


def report_numbers(report: str) -> List[ClaimNumber]:
    """Distinct quantities of a whole report, extracted per sentence so each one
    carries the identifiers of its own sentence."""
    out: List[ClaimNumber] = []
    seen: set = set()
    for chunk in re.split(r"(?<=[.;:!?])\s+|\n+", report or ""):
        for cn in extract_claim_numbers(chunk):
            key = (cn.canonical, cn.pct, cn.kind == "bound")
            if key in seen:
                continue
            seen.add(key)
            out.append(cn)
    return out


def ground_report(report: str, index: SourceIndex, *, coincidence_max: float = 0.05
                  ) -> List[QuantityGrounding]:
    return [ground_number(cn, index, coincidence_max=coincidence_max)
            for cn in report_numbers(report)]


def _read_records(work_dir, step_number: int) -> dict:
    """Union of the researcher's reads across attempts: path → richest record."""
    by_path: dict = {}
    if not work_dir:
        return by_path
    logs = Path(work_dir).expanduser() / "logs"
    per_attempt = sorted(logs.glob(f"step_{step_number}_researcher_tools_attempt*.json"))
    files = per_attempt or [logs / f"step_{step_number}_researcher_tools.json"]
    for f in files:
        if not f.is_file():
            continue
        try:
            recs = json.loads(f.read_text()).get("sources_read", [])
        except Exception:  # noqa: BLE001
            continue
        for s in recs:
            p = s.get("path", "?")
            cur = by_path.get(p)
            if cur is None:
                by_path[p] = dict(s)
                continue
            # merge ranges; keep the longest output
            r = list(cur.get("ranges") or []) + list(s.get("ranges") or [])
            if len(s.get("output") or "") > len(cur.get("output") or ""):
                cur.update({k: v for k, v in s.items() if k != "ranges"})
            if r:
                cur["ranges"] = r
    return by_path


def _stub_for(rec: dict, index: SourceIndex, stub_chars: int, work_dir) -> str:
    """A short head of one read artifact, headed by what the researcher saw of it.
    Re-read from disk when it still matches the sha the researcher recorded."""
    path = rec.get("path", "?")
    name = Path(path).name
    tool = rec.get("tool")
    if tool == "view_image":
        body = (rec.get("output") or "").strip()
        return f"**read off the figure {name}:**\n```\n{body[:stub_chars]}\n```"
    total = rec.get("chars") or rec.get("bytes") or "?"
    ranges = rec.get("ranges") or ([[0, len(rec.get("output") or "")]] if rec.get("output") else [])
    seen = ", ".join(f"{a}–{b}" for a, b in ranges) or "?"
    header = f"[researcher saw chars {seen} of {total}]"
    body = None
    try:
        p = Path(path)
        if not p.is_absolute() and work_dir:
            p = Path(work_dir).expanduser() / p
        if p.is_file() and rec.get("sha256"):
            raw = p.read_bytes()
            if hashlib.sha256(raw).hexdigest() == rec["sha256"]:
                body = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        body = None
    if body is None:
        body = rec.get("excerpt") or rec.get("output") or ""
        if rec.get("sha256"):
            header += " [source changed since the researcher read it]"
    body = body.strip()
    cut = body[:stub_chars]
    if len(body) > stub_chars:
        cut += f"\n[… {len(body) - stub_chars} chars omitted ({stub_chars}–{len(body)} of {len(body)}); full text: {path}]"
    return f"**read from {name}:** {header}\n```\n{cut}\n```"


def render_evidence(work_dir, step_number: int, report: str, index: SourceIndex, *,
                    ledger_chars: int = 24_000, stub_chars: int = 1_200,
                    coincidence_max: float = 0.05):
    """(read_sources_block, ledger_block, groundings) for a grader prompt.

    The stubs say WHAT the researcher opened and how much of it it saw; the
    ledger says, for every number in the report, where in the run's full
    sources it exists — or that it does not. Never raises."""
    try:
        recs = _read_records(work_dir, step_number)
        blocks = [_stub_for(r, index, min(stub_chars, 3_000), work_dir) for r in recs.values()]
        stubs = ""
        if blocks:
            stubs = (
                "----- ARTIFACTS THE RESEARCHER READ ----------\n"
                "The researcher opened these artifacts while writing the report. Each "
                "stub is a SHORT HEAD of the file — a value missing from a stub is NOT "
                "missing from the file; the QUANTITY LEDGER below is the authority on "
                "presence and absence.\n\n"
                + "\n\n".join(blocks)
                + "\n---------------------------------------------"
            )
        qs = ground_report(report, index, coincidence_max=coincidence_max)
        ledger = render_ledger(qs, index.coverage, max_chars=ledger_chars)
        return stubs, ledger, qs
    except Exception as e:  # noqa: BLE001
        print(f"[grounding] render_evidence error ({e!r})", file=sys.stderr, flush=True)
        return "", "", []


# ── verdict policy ────────────────────────────────────────────────────────

_RECEIPT_CAP = 8


# ── input-sourced description: the ONE place a model may weaken a finding ────
#
# THIS IS THE ONLY POINT IN THE ENGINE WHERE A MODEL'S JUDGEMENT CAN WEAKEN A
# DETERMINISTIC FINDING, and it can do so only inside the envelope below. Everywhere
# else the direction is fixed: deterministic grounding DEMOTES the LLM's verdicts
# (`apply_overrides`), the API pre-flight overrules the engineer, the static gates
# overrule the engineer, and the infeasibility audit
# (`self_debug/infeasibility_check.py`) overrules the step evaluator. If anyone later
# wants to widen this exception, THIS SENTENCE is where they should start arguing.
#
# Why it exists. `apply_overrides` keeps a claim hard when its numbers were located ONLY
# in a file the run was GIVEN, on the reading that "not computed by this run" is as good
# as absent. Two real fleet runs show that reading is right for one shape and wrong for
# the other, with the numbers coming from the SAME file:
#   * metabric Project9 step 6 — "The cohort outcome is characterized by a balance
#     between cancer-related and non-cancer deaths (646 Died of Disease vs. 496 Died of
#     Other Causes)", quoted correctly out of `input_files/data_description.md`. A
#     correct description of the provided cohort; charging it as a fabrication failed
#     five fulfilled steps and a finished report.
#   * project99 — "We established that atom counts alone explain nearly all variance in
#     total energies ($R^2 = 0.9999$)", numbers from the same file. A result the run
#     never computed, asserted as its own. Exactly the fabrication the rule guards.
# Provenance cannot separate them; the difference is what the sentence CLAIMS TO BE.
#
# The envelope. A demotion requires ALL of:
#   1. the numbers were located in an input file (decided here, deterministically);
#   2. the sentence carries NO result-assertion marker from the closed list below —
#      checked FIRST, so project99's "We established" is refused before any model is
#      consulted, and a sentence mixing description with assertion stays hard; and
#   3. a forced-choice classification quotes a span VERBATIM from the claim and calls it
#      a description of provided data rather than an asserted result — and the quoted
#      span is verified to be a real substring, so a model that cannot point at the text
#      it is judging is ignored.
# Any one of them absent leaves the claim hard. Every demotion is recorded with the span
# and the justification (`logs/step_N_input_sourced_demotions.json`, the step outcome,
# and `analysis_status.json`), so a human can audit what was waved through and a later
# analysis can count how often this fires.

# Closed, short and obvious on purpose. A phrase here means the sentence presents its
# numbers as something the run FOUND, which an input file cannot support no matter where
# the numbers came from. Deliberately NOT tuned to any one fixture: these are the ways a
# report says "we did this".
RESULT_ASSERTION_MARKERS = (
    "we established", "we have established", "established that",
    "we demonstrated", "we demonstrate", "demonstrated that", "demonstrates that",
    "we found", "we find", "we observed", "we observe",
    "we showed", "we show", "shows that", "showed that",
    "our analysis", "our results", "our findings", "our model",
    "we conclude", "we concluded", "concluded that",
    "confirms", "confirmed that", "reveals", "revealed that",
    "proves", "proven", "we measured", "we computed", "we calculated",
)


def asserts_a_result(statement: str) -> Optional[str]:
    """The result-assertion marker this sentence carries, or None.

    Condition 2 of the envelope above, and the only one that can refuse a demotion
    before a model is consulted. Case-insensitive, whitespace-collapsed substring match
    — a closed list, so it cannot drift into "anything that looks assertive"."""
    text = re.sub(r"\s+", " ", (statement or "")).casefold()
    for marker in RESULT_ASSERTION_MARKERS:
        if marker in text:
            return marker
    return None


def input_only_hard_claims(audit, groundings: dict) -> List[tuple]:
    """`(claim, [(value, token), ...])` for every claim `apply_overrides` left HARD
    because its numbers were located only in the run's INPUT files.

    Recomputes the same condition that function applies, so the two cannot disagree:
    at least one value located only in an input, and no value located nowhere at all.
    """
    out = []
    for c in getattr(audit, "claims", []) or []:
        if getattr(c, "verdict", None) != "unsupported_hard":
            continue
        g = groundings.get(getattr(c, "statement", ""))
        if g is None or g.rescuable or g.wrong_citation:
            continue
        values = [q for q in g.quantities if q.number.kind != "bound"]
        inp = [(q.number.text, q.input_only[0]) for q in values if not q.hits and q.input_only]
        if inp and all(q.hits or q.input_only for q in values):
            out.append((c, inp))
    return out


def _flag(claim) -> None:
    """Mark a ClaimCheck as overridden; tolerate a model without the field."""
    try:
        claim.grounding_override = True
    except Exception:  # noqa: BLE001
        pass


def _receipt(evidence: str, claim_numbers: List[ClaimNumber], index: SourceIndex,
             coincidence_max: float) -> Optional[str]:
    """A strongly-grounded number in the auditor's evidence that is NOT one of the
    claim's own numbers and sits next to the claim's own identifiers: the auditor
    pointed at a conflicting value of the SAME quantity (a receipt), so the
    accusation is about meaning, not presence. An unlabelled number in the
    evidence is not a receipt — the old auditor quoted counts and unrelated
    values freely. A citation the ledger verified beats any receipt."""
    own = {n.canonical for n in claim_numbers}
    idents = [w for n in claim_numbers for w in n.idents]
    for cn in extract_claim_numbers(evidence)[:_RECEIPT_CAP]:
        if cn.canonical in own or cn.kind == "bound":
            continue
        cn.idents = idents or cn.idents
        q = ground_number(cn, index, coincidence_max=coincidence_max)
        if q.best == "strong" and any("labelled" in h.reason for h in q.hits):
            h = next(h for h in q.hits if "labelled" in h.reason)
            return f"{cn.text} at {h.token.source}:{h.token.line}"
    return None


def apply_overrides(audit, groundings: dict, coverage: Coverage, *, index: Optional[SourceIndex] = None,
                    coincidence_max: float = 0.05, accused: Optional[Iterable[str]] = None):
    """Demote the LLM's `unsupported_hard` verdicts that deterministic grounding
    refutes. Mutates `audit` in place; returns a list of (claim, action) notes.

    A hard claim whose numbers exist in this run's outputs becomes
    `unsupported_weak` (advisory) unless the auditor's evidence carries a
    receipt — a strongly-grounded conflicting number — or the citation is wrong.
    Hard stands for deterministic absence (`ungrounded`/`partial`), for
    `no_quantities` (the LLM is the only judge of prose), and for input-only
    numbers (present, but not computed by this run — the evidence says so).
    A claim already charged this step (`accused` fingerprints) is never
    re-charged: grounded → `supported`, still absent → left hard, annotated,
    and the caller skips the writer round-trip (the livelock breaker).
    """
    notes = []
    accused = set(accused or ())
    try:
        for c in getattr(audit, "claims", []) or []:
            if getattr(c, "verdict", None) != "unsupported_hard":
                continue
            g = groundings.get(c.statement)
            if g is None:
                continue
            repeat = g.fingerprint in accused
            values = [q for q in g.quantities if q.number.kind != "bound"]
            if g.rescuable and not g.wrong_citation:
                receipt = None
                if index is not None and c.evidence and not g.cited_and_verified:
                    receipt = _receipt(c.evidence, [q.number for q in g.quantities], index, coincidence_max)
                if receipt and not repeat:
                    c.evidence = f"[grounding: numbers present; auditor cites {receipt}] " + c.evidence
                    notes.append((c, "stands: receipt"))
                    continue
                where = "; ".join(
                    f"{q.number.text} at {q.hits[0].token.source}:{q.hits[0].token.line}"
                    for q in values if q.hits)
                if g.state == "bounds_only":
                    where = "; ".join(g.bound_checks) or "bound only"
                c.verdict = "supported" if repeat else "unsupported_weak"
                _flag(c)
                c.evidence = (f"[grounding override: {g.state} — {where}] auditor said: {c.evidence}")
                notes.append((c, "override→supported" if repeat else "override→weak"))
                continue
            if g.wrong_citation:
                c.evidence = "[grounding: numbers exist elsewhere in this run, but NOT in the cited file] " + c.evidence
                notes.append((c, "stands: wrong citation"))
                continue
            inp = [(q.number.text, q.input_only[0]) for q in values if not q.hits and q.input_only]
            if inp and all(q.hits or q.input_only for q in values):
                c.evidence = "; ".join(
                    f"{t} appears only in `{tok.source}:{tok.line}` — not computed by this run"
                    for t, tok in inp) + ". " + c.evidence
                notes.append((c, "stands: input-only"))
                continue
            if g.deterministically_absent and coverage.incomplete:
                c.verdict = "unsupported_weak"
                _flag(c)
                c.evidence = (f"[grounding index incomplete ({coverage.describe()}): absence not proven] "
                              + c.evidence)
                notes.append((c, "override→weak (index incomplete)"))
                continue
            missing = [q.number.text for q in values if not q.hits]
            if missing:
                c.evidence = (f"[grounding: {', '.join(missing)} NOT FOUND in any artifact "
                              f"({coverage.describe()})] " + c.evidence)
            notes.append((c, "stands: " + g.state))
    except Exception as e:  # noqa: BLE001
        print(f"[grounding] apply_overrides error ({e!r})", file=sys.stderr, flush=True)
    return notes


def ground_claims(audit, index: SourceIndex, *, coincidence_max: float = 0.05) -> dict:
    """statement → ClaimGrounding for every claim in an audit."""
    out: dict = {}
    for c in getattr(audit, "claims", []) or []:
        s = getattr(c, "statement", "") or ""
        if s not in out:
            out[s] = ground(s, index, coincidence_max=coincidence_max)
    return out


def truncation_marker(omitted: int, a: int, b: int, total: int, path: Optional[str] = None) -> str:
    """The one marker every cut renders, so no window is ever an unmarked prefix."""
    s = f"[… {omitted} chars omitted ({a}–{b} of {total})"
    if path:
        s += f"; full text: {path}"
    return s + "]"


__all__ = [
    "Tier", "NumToken", "SourceIndex", "Coverage", "ClaimNumber", "Hit",
    "QuantityGrounding", "ClaimGrounding", "knob", "canonical", "unwrap",
    "extract_claim_numbers", "ground", "ground_number", "ground_report",
    "ground_claims", "render_ledger", "render_evidence", "apply_overrides",
    "fingerprint", "index_for_step", "truncation_marker", "report_numbers",
]
