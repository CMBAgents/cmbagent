"""Ask the ENVIRONMENT what an API actually is, instead of guessing from memory.

Deterministic only (importlib + dir() + inspect.signature + difflib) — no model call, so a
guess can never be laundered into an authoritative-looking answer. The answer must come from
the environment the code will RUN in, so the reports are self-contained stdlib-only source
(`SNIPPET` and friends) that is exec'd locally or shipped to a remote sandbox.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import sys
from pathlib import Path

REMOTE_ENV = "CMBAGENT_LG_REMOTE_EXECUTOR"


def env_report(snippet: str, spec: dict, work_dir, local) -> str:
    """The report for `spec`, from the environment the code actually RUNS in.

    Locally that is this process, via `local()`. Under `CMBAGENT_LG_REMOTE_EXECUTOR` the engine
    venv is a different package set, so the same `snippet` the local path execs is run in the
    guest sandbox and its stdout is the report. "" whenever the guest cannot answer — a caller
    must then stay silent rather than fall back to the engine venv.
    """
    remote = os.environ.get(REMOTE_ENV)
    if not remote:
        return local()
    if not work_dir:
        return ""
    try:
        from cmbagent_lg.self_debug.remote_exec import RemoteExecutor
        reply = RemoteExecutor.from_env(remote).introspect(
            Path(work_dir).name, snippet, [json.dumps(spec)]
        )
    except Exception as e:  # noqa: BLE001 — introspection must never break the step
        print(f"[api_introspect] remote introspection skipped: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""
    if reply.get("type") != "introspect_result" or reply.get("returncode") != 0:
        return ""
    return (reply.get("stdout") or "").strip()

# Executed INSIDE the environment being asked about (locally via exec, remotely by shipping the
# text to the guest). Must stay self-contained and stdlib-only: no imports from this package,
# and no dependency on how it is transported.
SNIPPET = r'''
import difflib, importlib, inspect, json, pkgutil, re, sys

def _is_submodule(module_name, symbol):
    # A submodule is not an ATTRIBUTE until something imports it: `from scipy import special` is
    # valid while `hasattr(scipy, "special")` is False in a fresh interpreter. Only the environment
    # that HAS the package can settle this, so it has to happen here.
    try:
        importlib.import_module(module_name + "." + symbol)
        return True
    except Exception:
        return False

def _report(module_name, symbol=None, max_names=60):
    out = []
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        # A failed import is itself the answer — report it rather than raising.
        return f"MODULE {module_name!r} could NOT be imported: {type(e).__name__}: {e}"
    # Version: a SUBMODULE (scipy.special) carries no __version__, so walk up to the root package —
    # the version is the single most useful fact here, and reporting None for it wastes the call.
    ver = getattr(mod, "__version__", None) or getattr(mod, "version", None)
    root_name = module_name.split(".")[0]
    if not isinstance(ver, str):
        try:
            ver = getattr(importlib.import_module(root_name), "__version__", None)
        except Exception:
            ver = None
    if not isinstance(ver, str):
        try:
            from importlib.metadata import version as _pkgver
            ver = _pkgver(root_name)
        except Exception:
            ver = None
    out.append(f"MODULE {module_name}  ({root_name} version={ver!r})  file={getattr(mod, '__file__', '?')}")

    public = sorted(n for n in dir(mod) if not n.startswith("_"))

    if symbol:
        if hasattr(mod, symbol):
            obj = getattr(mod, symbol)
            out.append(f"EXISTS: {module_name}.{symbol}  type={type(obj).__name__}")
            try:
                out.append(f"SIGNATURE: {symbol}{inspect.signature(obj)}")
            except (TypeError, ValueError):
                out.append("SIGNATURE: <not introspectable>")
            doc = (inspect.getdoc(obj) or "").strip().splitlines()
            if doc:
                out.append("DOC (first lines):")
                out += ["  " + l for l in doc[:8]]
        elif _is_submodule(module_name, symbol):
            out.append(f"EXISTS: {module_name}.{symbol}  type=module")
        else:
            # THE case this tool exists for: the name is gone. Give the real candidates so the
            # caller adapts to what IS present instead of retrying what is not.
            out.append(f"ABSENT: {module_name}.{symbol} does NOT exist in the INSTALLED version.")
            # The exact name may live on a PARENT package (`from pkg.sub import X` where X is
            # `pkg.X`): say so first, and let only a strong difflib hit compete with it.
            elsewhere = []
            parts = module_name.split(".")
            for i in range(len(parts) - 1, 0, -1):
                anc = sys.modules.get(".".join(parts[:i]))
                if anc is not None and hasattr(anc, symbol):
                    elsewhere.append(".".join(parts[:i]))
            for anc_name in elsewhere:
                out.append(f"NOTE: '{symbol}' EXISTS as {anc_name}.{symbol} — import it from there.")
            # A package's submodules are not attributes until something imports them, so
            # `dir()` leaves out the very names a missing submodule is most often confused
            # with (`pkg.sub.fitfunctions` vs `fit_function`). List them from disk.
            subs = []
            try:
                if hasattr(mod, "__path__"):
                    subs = sorted({m_.name for m_ in pkgutil.iter_modules(mod.__path__)
                                   if not m_.name.startswith("_")})[:200]
            except Exception:
                subs = []
            public = sorted(set(public) | set(subs))
            close = difflib.get_close_matches(symbol, public, n=8, cutoff=0.5)
            # KEEP THIS BLOCK LITERALLY IDENTICAL to the one in CLASS_SNIPPET's `_render`: the two
            # snippets are separate strings by design (each must be self-contained), so the only
            # protection against drift is that the text matches.
            # difflib alone finds RENAMES that look alike (sph_harm -> sph_harm_y) but misses
            # SEMANTIC renames that share no spelling (lpmn -> assoc_legendre_p). Add substring
            # and stem matches so the real replacement is surfaced, not buried in 365 names.
            low = symbol.lower().strip("_")
            stems = [low] + [low[:i] for i in range(len(low), 2, -1)][:4]
            # Truncating the WHOLE name never reaches a rename that shares only a leading WORD.
            # Measured in biopython 1.87: difflib.get_close_matches("CodonUsage", dir(Bio.SeqUtils),
            # cutoff=0.5) is [], and the truncation stems stop at `codonus`, which is not a
            # substring of `codonadaptationindex` — so the report said "No similarly-named symbol
            # found" while CodonAdaptationIndex sat in the very same module. Split the name into
            # words (underscores + camelCase) and prefix-match the leading ones instead.
            # 4-char floor: shorter leading words are generic verbs (get/set/add/to/is) that match
            # a large fraction of any module and would push the real candidate past the cap.
            words = [w.lower() for part in symbol.split("_")
                     for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[^A-Z_]+", part)]
            heads = [w for w in words[:2] if len(w) >= 4]
            lows = [(n_, n_.lower()) for n_ in public]
            # Ordering is load-bearing in a prompt: difflib's hits keep the front, then the
            # leading-word matches (the ones that carry a SEMANTIC rename), then the weaker
            # substring stems. The cap truncates the tail, never a difflib hit.
            for n_, nl in lows:
                if n_ not in close and any(nl.startswith(h) for h in heads):
                    close.append(n_)
            for n_, nl in lows:
                if n_ not in close and any(st and (st in nl or nl.startswith(st)) for st in stems):
                    close.append(n_)
            close = close[:12]
            if elsewhere:
                # The exact name was found on a parent: only a STRONG lookalike may compete
                # with it, so a weak difflib hit does not read as "the replacement".
                close = [c for c in close
                         if difflib.SequenceMatcher(None, symbol, c).ratio() >= 0.75]
            if close:
                out.append("CLOSEST EXISTING NAMES (verify the signature AND the summary line "
                           "before substituting — a renamed function may take its arguments in a "
                           "DIFFERENT ORDER, or return a different quantity):")
                for c in close:
                    obj_ = getattr(mod, c, None)
                    if c in subs and (obj_ is None or inspect.ismodule(obj_)):
                        out.append(f"  {c}  (submodule: import {module_name}.{c})")
                        continue
                    try:
                        sig = str(inspect.signature(obj_))
                    except Exception:
                        sig = "(...)"
                    # The first docstring line, because a signature alone often cannot separate two
                    # plausible renames: two candidates can take the same arguments and differ only
                    # in WHAT THEY RETURN (a fraction where the old name returned a percentage), and
                    # the summary line is the only place that difference is stated.
                    try:
                        doc_ = (inspect.getdoc(obj_) or "").strip().splitlines()
                        first = doc_[0].strip() if doc_ else ""
                    except Exception:
                        first = ""
                    if len(first) > 100:
                        first = first[:97] + "..."
                    out.append(f"  {c}{sig}" + (f"\n      {first}" if first else ""))
            else:
                out.append("No similarly-named symbol found. Public names are listed below.")
            if subs:
                out.append(f"SUBMODULES OF {module_name} ({len(subs)}): " + ", ".join(subs[:max_names])
                           + (" ..." if len(subs) > max_names else ""))
            out.append(f"ALL PUBLIC NAMES ({len(public)}):")
            out.append("  " + ", ".join(public[:max_names])
                       + (" ..." if len(public) > max_names else ""))
    else:
        out.append(f"PUBLIC NAMES ({len(public)}):")
        out.append("  " + ", ".join(public[:max_names])
                   + (" ..." if len(public) > max_names else ""))
    return "\n".join(out)

if __name__ == "__main__":
    spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(_report(spec.get("module", ""), spec.get("symbol") or None))
'''


def introspect_local(module: str, symbol: str | None = None, max_names: int = 60) -> str:
    """Run the report against THIS process's environment (correct for local execution).

    `max_names` bounds the public-name inventory. The default suits the ABSENT case, where it is
    a fallback under a short candidate list; a caller listing a whole module as its PRIMARY
    content (the proactive digest) raises it, since a truncated name list is near useless.
    """
    ns: dict = {}
    exec(compile(SNIPPET, "<api_introspect>", "exec"), ns)
    try:
        return ns["_report"](module, symbol or None, max_names)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        return f"introspection failed: {type(e).__name__}: {e}"


# ── retry-time injection ─────────────────────────────────────────────────────
#
# On an API-SHAPED failure this hook computes the deterministic report and injects it into the
# engineer's retry context, with no extra model call. Gated OFF by default.

# MODULE-level shapes. All name the module AND the symbol explicitly, so the lookup is read off
# the traceback rather than inferred. The patterns are the message TAILS in CPython's own
# wording, without the exception-name prefix, so they also match a caught-and-printed `str(e)`
# (see `_BARE_PATTERNS`).
_MODULE_ATTR = r"module '(?P<module>[\w.]+)' has no attribute '(?P<symbol>\w+)'"
_IMPORT_NAME = r"cannot import name '(?P<symbol>\w+)' from '(?P<module>[\w.]+)'"
# A DOTTED ModuleNotFoundError is the module shape too: the LEAF was removed from an installed
# parent, and both are in the text. The leading `[\w.]+\.` REQUIRES a dot — a bare `No module
# named 'leidenalg'` names an uninstalled PACKAGE, a different and recoverable failure that
# preflight/escalation own, and must keep returning None.
_MISSING_SUBMODULE = r"No module named '(?P<module>[\w.]+)\.(?P<symbol>\w+)'"
# INSTANCE-level shape: a method removed from a class. Names only the TYPE, not the module it
# came from, so it is resolved from the failing SCRIPT's own imports (`_resolve` in
# CLASS_SNIPPET); with no code, `retry_report` leaves this shape alone entirely.
_INSTANCE_ATTR = r"'(?P<type>\w+)' object has no attribute '(?P<attr>\w+)'"

# CALL-SHAPE: a REAL callable, given a keyword its signature does not accept. Python's own call
# binding raises this only for a signature with NO `**kwargs` — a permissive one swallows the
# keyword and fails deeper, wearing a different face (see `api_preflight.py`'s call-shape
# section for why that is a hard boundary, not a miss). Names only the bare function, so the
# module is resolved from the script's call sites via `resolve_dotted_call_by_func_name`.
# CPython ≥3.10 qualifies a METHOD with the class that defines it (`Mixin.score()`,
# `Thing.__init__()`); the optional `cls` group captures it, and `func` stays the bare
# name every existing caller expects.
_UNEXPECTED_KWARG = (r"(?:(?P<cls>\w+)\.)?(?P<func>\w+)\(\) got an unexpected keyword argument "
                     r"'(?P<kw>\w+)'")
# The arity counterpart, same reasoning: only raised for a signature with no `*args`.
_BAD_ARITY = (r"(?:(?P<cls>\w+)\.)?(?P<func>\w+)\(\) takes [^\n']*positional argument[^\n']* "
              r"but \d+ (?:was|were) given")

# artist_kwarg shape: `AttributeError: <ClassName>.set() got an unexpected keyword argument
# '<kw>'` is NOT Python's call binding but matplotlib's own kwarg validation
# (`Artist._update_props`), which raises exactly this text iff `set_<kw>` is not callable on the
# instance. So checking `hasattr(cls, f"set_{kw}")` re-runs the same test the crash ran, rather
# than guessing. Note the class name is embedded, unlike CPython's own TypeError wording.
_ARTIST_SET_KWARG = r"(?P<cls>\w+)\.set\(\) got an unexpected keyword argument '(?P<kw>\w+)'"

# The missing-argument counterpart, raised by Python's own call binding whatever the signature's
# `*args`/`**kwargs`: `modelTinker10() missing 1 required positional argument: 'z'`. The names
# are quoted and joined with ", " and " and ".
_MISSING_ARG = (r"(?:(?P<cls>\w+)\.)?(?P<func>\w+)\(\) missing \d+ required "
                r"(?:positional|keyword-only) arguments?: (?P<names>'\w+'(?:(?:,? and |, )'\w+')*)")

# Shapes that name no function, or name it only through the failing script line. Each is
# answered from the class or callable the script line and the traceback point at, and stays
# silent when that resolution is not unique (see SHAPE_SNIPPET).
# Assigning a property that has no setter (CPython >= 3.11 wording).
_NO_SETTER = r"property '(?P<attr>\w+)' of '(?P<type>\w+)' object has no setter"
# Calling a value: `pars.N_eff()` where `N_eff` is a property returning a float.
_NOT_CALLABLE = r"'(?P<type>\w+)' object is not callable"
# ctypes' own arity check on a foreign function with `argtypes`; it names no function.
_CTYPES_ARITY = r"this function takes at least (?P<n>\d+) arguments? \((?P<m>\d+) given\)"
# A value of the wrong type for a typed field or parameter, raised inside the library: ctypes
# Structure fields (`expected AccuracyParams instance, got int`) and C-level float conversion.
_EXPECTED_INSTANCE = r"expected (?P<type>\w+) instance, got (?P<got>\w+)"
_REAL_NUMBER = r"must be real number, not (?P<got>\w+)"
# A library's OWN unknown-argument exception, raised by a `**kwargs` forwarder after it found no
# home for a keyword (`camb.baseconfig.CAMBUnknownArgumentError: Unrecognized parameter: n_eff`).
# Anchored on an exception CLASS name that says so, never on free message text.
_UNKNOWN_ARG = (r"^(?:[\w.]+\.)?(?P<exc>\w*(?:Unknown|Unrecogni[sz]ed|Unexpected|Invalid)\w*"
                r"(?:Argument|Parameter|Param|Keyword|Kwarg|Option)\w*): "
                r"(?:[^\n:]*?(?:argument|parameter|keyword|kwarg|option)s?\s*:?\s*)?"
                r"['\"]?(?P<kw>[A-Za-z_]\w*)['\"]?\s*$")

# STDERR patterns: anchored on the exception name, i.e. a real traceback.
_ERROR_PATTERNS = (
    # AttributeError: module 'scipy.special' has no attribute 'sph_harm'
    re.compile("AttributeError: " + _MODULE_ATTR),
    # ImportError: cannot import name 'lpmn' from 'scipy.special' (/path/__init__.py)
    re.compile("ImportError: " + _IMPORT_NAME),
    # ModuleNotFoundError: No module named 'Bio.SeqUtils.CodonUsage'
    re.compile("ModuleNotFoundError: " + _MISSING_SUBMODULE),
)
_INSTANCE_PATTERN = re.compile("AttributeError: " + _INSTANCE_ATTR)
_SIGNATURE_KWARG_PATTERN = re.compile("TypeError: " + _UNEXPECTED_KWARG)
_SIGNATURE_ARITY_PATTERN = re.compile("TypeError: " + _BAD_ARITY)
_ARTIST_SET_PATTERN = re.compile("AttributeError: " + _ARTIST_SET_KWARG)
_SIGNATURE_MISSING_PATTERN = re.compile("TypeError: " + _MISSING_ARG)
_NO_SETTER_PATTERN = re.compile("AttributeError: " + _NO_SETTER)
_NOT_CALLABLE_PATTERN = re.compile("TypeError: " + _NOT_CALLABLE)
_CTYPES_ARITY_PATTERN = re.compile("TypeError: " + _CTYPES_ARITY)
_EXPECTED_INSTANCE_PATTERN = re.compile("TypeError: " + _EXPECTED_INSTANCE)
_REAL_NUMBER_PATTERN = re.compile("TypeError: " + _REAL_NUMBER)
_UNKNOWN_ARG_PATTERN = re.compile(_UNKNOWN_ARG, re.M)
_PATTERNS = (
    tuple(("module", p) for p in _ERROR_PATTERNS)
    + (("instance", _INSTANCE_PATTERN),)
    + (("signature_kw", _SIGNATURE_KWARG_PATTERN), ("signature_arity", _SIGNATURE_ARITY_PATTERN),
       ("signature_missing", _SIGNATURE_MISSING_PATTERN))
    + (("artist_kwarg", _ARTIST_SET_PATTERN),)
    + (("property_setter", _NO_SETTER_PATTERN), ("not_callable", _NOT_CALLABLE_PATTERN),
       ("ctypes_arity", _CTYPES_ARITY_PATTERN), ("value_type", _EXPECTED_INSTANCE_PATTERN),
       ("value_type", _REAL_NUMBER_PATTERN), ("unknown_argument", _UNKNOWN_ARG_PATTERN))
)

# STDOUT patterns: the same shapes with the exception name absent — what a script that catches
# its own exception and prints `str(e)` produces (exit 0, empty stderr). Accepted for STDOUT
# only; the stderr path stays anchored to a real traceback.
#
# The CALL-SHAPE and artist_kwarg patterns are deliberately excluded: without the exception
# class name, "got an unexpected keyword argument" is ambiguous — Python's call binding raises
# it as `TypeError` (the provable case), while matplotlib raises the identical text as
# `AttributeError` from its own kwarg handling, which must stay silent. The module/instance
# shapes carry no such ambiguity.
_BARE_PATTERNS = (
    ("module", re.compile(_MODULE_ATTR)),
    ("module", re.compile(_IMPORT_NAME)),
    ("module", re.compile(_MISSING_SUBMODULE)),
    ("instance", re.compile(_INSTANCE_ATTR)),
)

_HEADER = (
    "**API REPORT — deterministic introspection of the INSTALLED package (importlib + "
    "inspect, no model involved). This is what the environment ACTUALLY contains; trust it "
    "over your recollection of the library:**"
)
_STEER = (
    "The failure above is an API mismatch, not a logic bug: the name is simply not in the "
    "installed release. Adapt the code to a name that EXISTS (check its signature — a "
    "renamed function may take its arguments in a different order). Do not resubmit the "
    "same call, and do not work around it by inventing data."
)


def kwarg_error_qualifier(stderr: str | None) -> str | None:
    """The class qualifier (`Mixin` in `Mixin.score() got an unexpected keyword argument`) of the
    call-shape TypeError `classify_api_error` picks from `stderr`, or None when it picks another
    shape or the message names a bare function. Companion to `classify_api_error`, whose tuple
    shape is fixed."""
    best = _last_match(stderr)
    if best is None or not best[0].startswith("signature"):
        return None
    return best[1].groupdict().get("cls")


# The shapes `classify_api_error` recognised before the stderr-only ones below them in
# `_PATTERNS`. Every reader of its answer (api_history's facts, the bypass-after-fault guard)
# was written against these, so one of them, when present anywhere in the text, still wins.
_ORIGINAL_KINDS = frozenset({"module", "instance", "signature_kw", "signature_arity",
                             "artist_kwarg"})


# ── library errors embedded in other text (the retry report only) ──────────────────────────
#
# A script that wraps a library call in `except Exception` and raises its own error keeps the
# library's error only as TEXT inside the new message, and a script that prints the error it
# caught leaves no exception line at all. Three generic Python forms are read back, each into
# the canonical `Name: message` line the anchored patterns above already understand:
#
#   * an exception REPR anywhere in the text — `AttributeError("property 'x' of 'P' object has
#     no setter")`, also with escaped quotes when it sits inside another repr. The class name is
#     there, so every shape applies exactly as on a traceback's own last line;
#   * the bare message of an unambiguous shape (`_EMBEDDED_BARE`) inside the MESSAGE of another
#     exception's line — `RuntimeError: could not configure: property 'x' of 'P' object has no
#     setter`. The call-shape and artist_kwarg messages are not among them, for the reason given
#     at `_BARE_PATTERNS`;
#   * on stdout (a printed `str(e)`), the property-setter message as well as `_BARE_PATTERNS`.
#
# Chained tracebacks ("The above exception was the direct cause of the following exception",
# "During handling of the above exception, another exception occurred") keep every exception's
# own line, so the anchored patterns see them; `_hit_segment` hands each hit the traceback it
# belongs to, so a shape answered from the script frame reads the right one.
# `classify_api_error` and its other readers (the observed-absence store, the bypass guard)
# are unchanged: only `retry_report` asks for these forms.
_EXC_NAME = r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception)"
_REPR_RE = re.compile(r"(?<![\w.])(?P<exc>" + _EXC_NAME + r")\((?P<esc>\\{0,2})(?P<q>[\"'])"
                      r"(?P<msg>[^\n]*?)(?P=esc)(?P=q),?\)")
_EXC_LINE_RE = re.compile(r"^[ \t]*(?:" + _EXC_NAME + r"): (?P<msg>[^\n]+)$", re.M)
_EMBEDDED_BARE = _BARE_PATTERNS + (("property_setter", re.compile(_NO_SETTER)),)
_STDOUT_BARE_LATER = (("property_setter", re.compile(_NO_SETTER)),)


def _unescape(msg: str) -> str:
    """The message of a repr, with the escaping of up to two repr levels undone."""
    for _ in range(2):
        msg = msg.replace("\\\\", "\x00").replace("\\'", "'").replace('\\"', '"').replace("\x00", "\\")
    return msg


def _embedded_matches(text: str, anchored: list, allow_bare: bool) -> list:
    """The `(start, kind, match)` hits of the embedded forms described above; `start` is where the
    form begins in `text`, `match` is taken on the canonical line it was read into. A form an
    anchored hit already covers is skipped."""
    spans = [(m.start(), m.end()) for _s, _k, m in anchored]
    covered = lambda pos: any(a <= pos < b for a, b in spans)  # noqa: E731
    out, reprs = [], []
    pos = 0
    while True:   # a repr inside a repr's message is read too: search on from inside each one
        r = _REPR_RE.search(text, pos)
        if r is None:
            break
        pos = r.start() + 1
        if covered(r.start()):
            continue
        line = "%s: %s" % (r.group("exc"), _unescape(r.group("msg")))
        for kind, pat in _PATTERNS:
            m = pat.search(line)
            if m is not None:
                out.append((r.start(), kind, m))
        reprs.append((r.start(), r.end()))
    spans += reprs
    for ln in _EXC_LINE_RE.finditer(text):
        base = ln.start("msg")
        for kind, pat in _EMBEDDED_BARE:
            for m in pat.finditer(ln.group("msg")):
                if not covered(base + m.start()):
                    out.append((base + m.start(), kind, m))
    if allow_bare:
        for kind, pat in _STDOUT_BARE_LATER:
            for m in pat.finditer(text):
                if not covered(m.start()) and not any(s == m.start() for s, _k, _m in out):
                    out.append((m.start(), kind, m))
    return out


def _hit_segment(text: str | None, start) -> str | None:
    """The part of `text` a hit at `start` belongs to: from the last traceback header before it to
    the end of its line, so a chained traceback's earlier exception is read with its own frames.
    `text` unchanged when no traceback precedes the hit (a printed error) or `start` is None."""
    if not text or start is None:
        return text
    tb = text.rfind("Traceback (most recent call last):", 0, start + 1)
    if tb < 0:
        return text
    end = text.find("\n", start)
    return text[tb:] if end < 0 else text[tb:end + 1]


def _all_matches(text: str | None, allow_bare: bool = False, embedded: bool = False) -> list:
    """Every recognised error in `text` as `(start, kind, match)`, in text order. `embedded` also
    reads the library errors embedded in other text (see above); the retry report only."""
    out = []
    for kind, pat in _PATTERNS + (_BARE_PATTERNS if allow_bare else ()):
        for m in pat.finditer(text or ""):
            out.append((m.start(), kind, m))
    if embedded and text:
        out += _embedded_matches(text, list(out), allow_bare)
    out.sort(key=lambda x: x[0])
    return out


def _pick(hits: list):
    """The hit `_last_match` answers with: the last among `_ORIGINAL_KINDS`, else the last."""
    orig = [h for h in hits if h[1] in _ORIGINAL_KINDS]
    return (orig or hits or [None])[-1]


def _last_match(text: str | None, allow_bare: bool = False) -> tuple[str, "re.Match"] | None:
    """`(kind, match)` for the LAST recognised error in `text` among `_ORIGINAL_KINDS`, else
    the last of the later kinds, else None. The one matcher behind `classify_api_error` (fixed
    tuple shape) and `kwarg_error_qualifier`. A later kind never displaces an original one: in
    a chained traceback (`np.trapz` raising, the `except` branch then calling None) the original
    hit is the one every reader can act on."""
    best = _pick(_all_matches(text, allow_bare))
    return (best[1], best[2]) if best is not None else None


def classify_api_error(stderr: str | None, allow_bare: bool = False) -> tuple[str, str, str] | None:
    """`("module", module, symbol)` / `("instance", type_name, attribute)` /
    `("signature", func_name, invalid_kwarg_or_empty)` / `("artist_kwarg", class_name, kwarg)` /
    `("property_setter", type_name, attribute)` / `("not_callable", type_name, "")` /
    `("ctypes_arity", n_expected, n_given)` / `("value_type", expected_type_or_"real number",
    given_type)` / `("unknown_argument", exception_name, keyword)` / None.

    The `"signature"` third element is `""` for the arity and missing-argument forms, which name
    no keyword. Returns the LAST recognised error in the text — a traceback ends with the
    exception that actually killed the run. `allow_bare` also accepts the message with no
    exception-name prefix (a caught-and-printed `str(e)`); reserved for STDOUT, since stderr
    carries a real traceback. The shapes after `artist_kwarg` are stderr-only.
    """
    best = _last_match(stderr, allow_bare)
    if best is None:
        return None
    kind, m = best
    if kind == "property_setter":
        return ("property_setter", m.group("type"), m.group("attr"))
    if kind == "not_callable":
        return ("not_callable", m.group("type"), "")
    if kind == "ctypes_arity":
        return ("ctypes_arity", m.group("n"), m.group("m"))
    if kind == "value_type":
        exp = m.groupdict().get("type") or "real number"
        return ("value_type", exp, m.group("got"))
    if kind == "unknown_argument":
        return ("unknown_argument", m.group("exc"), m.group("kw"))
    if kind == "signature_missing":
        return ("signature", m.group("func"), "")
    if kind == "module":
        return ("module", m.group("module"), m.group("symbol"))
    if kind == "instance":
        return ("instance", m.group("type"), m.group("attr"))
    if kind == "artist_kwarg":
        return ("artist_kwarg", m.group("cls"), m.group("kw"))
    if kind == "signature_kw":
        return ("signature", m.group("func"), m.group("kw"))
    return ("signature", m.group("func"), "")  # signature_arity: no specific keyword name


def parse_api_error(stderr: str | None) -> tuple[str, str] | None:
    """`(module, symbol)` for a MODULE-level API failure in `stderr`, else None.

    Kept for callers that only handle the module shape; `classify_api_error` also reports the
    instance-attribute shape.
    """
    hit = classify_api_error(stderr)
    return (hit[1], hit[2]) if hit and hit[0] == "module" else None


def _imported_modules(code: str | None) -> tuple[list[str], list[tuple[str, str]]]:
    """`(modules, [(module, imported_name)])` the script ACTUALLY imports, read with `ast`.

    Star and relative imports are skipped: neither names an origin resolvable here.
    """
    mods: list[str] = []
    froms: list[tuple[str, str]] = []
    try:
        tree = ast.parse(code or "")
    except SyntaxError:  # the script that CRASHED at runtime parsed fine, but be safe
        return mods, froms
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods.append(node.module)
            froms += [(node.module, a.name) for a in node.names if a.name != "*"]
    return mods, froms


# Resolution + report for the instance shape. Like `SNIPPET`, executed INSIDE the environment
# being asked about — same contract: self-contained, stdlib-only, prints text. It cannot be
# ordinary module code, because the classes it inspects must be the ones the CODE ran against.
#
# `spec` = {"type": "DataFrame", "attr": "append", "modules": [...], "froms": [[mod, name]]},
# read off the failing script with `ast` (`_imported_modules`, which stays engine-side).
CLASS_SNIPPET = r'''
import difflib, importlib, inspect, json, re, sys

def _resolve(type_name, modules, froms):
    """The class `type_name` refers to, PROVEN from the script's OWN imports — or None.

    Only modules the script itself imported are asked, and only for a class whose __name__ is
    exactly the name in the traceback. Deduped by object IDENTITY, so `import pandas`,
    `from pandas import DataFrame` and `pandas.core.frame` all name the SAME class and still
    count as ONE unambiguous answer. Zero matches, or two DIFFERENT classes sharing a name
    (logging.Formatter vs string.Formatter), yields None: picking one would be a guess.
    """
    found = {}
    for module_name, attr in [(m, type_name) for m in modules] + [tuple(f) for f in froms]:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue          # a module that will not import cannot answer
        obj = getattr(mod, attr, None)
        if inspect.isclass(obj) and obj.__name__ == type_name:
            found.setdefault(id(obj), (module_name + "." + attr, obj))
    return list(found.values())[0] if len(found) == 1 else None

def _class_report(spec, max_names=60):
    """Report for a REMOVED method, or "" when nothing can be PROVEN (caller injects nothing)."""
    type_name, attr = spec.get("type", ""), spec.get("attr", "")
    resolved = _resolve(type_name, spec.get("modules", []), spec.get("froms", []))
    if resolved is None:
        return ""
    label, cls = resolved
    # The attribute IS on the class: this traceback has some other cause (an instance
    # attribute shadowed, a subclass, a property raising AttributeError internally). We
    # cannot explain it, so say nothing rather than something plausible-sounding.
    if hasattr(cls, attr):
        return ""
    return _render(cls, label, type_name, attr, max_names)

def _defining_class(cls, name):
    """The MRO class that actually DEFINES `name`, or None. Never raises.

    `dir(cls)` flattens the MRO, so a class that subclasses a builtin reports its own API and
    the builtin's as one undifferentiated list. The first `vars()` in MRO order containing the
    name is where the attribute really comes from.
    """
    try:
        for klass in getattr(cls, "__mro__", [cls]):
            if name in vars(klass):
                return klass
    except Exception:
        pass
    return None

def _split_own(cls, public):
    """`(own, inherited_from_builtin, [builtin base names])` — never raises.

    Example: `Bio.SeqUtils.CodonAdaptationIndex` subclasses `dict`, so most of its public
    attributes are inherited dict methods (clear/get/keys/…) while its own real API methods
    (calculate, optimize) sit unmarked among them. Presenting all of them as equals buries the
    real answer. `object` counts as builtin too, though it contributes no public names.
    """
    own, inherited, bases = [], [], []
    try:
        for klass in getattr(cls, "__mro__", [cls]):
            if klass is not object and getattr(klass, "__module__", "") == "builtins":
                bases.append(klass.__name__)
    except Exception:
        pass
    for n in public:
        owner = _defining_class(cls, n)
        if owner is not None and (owner is object
                                  or getattr(owner, "__module__", "") == "builtins"):
            inherited.append(n)
        else:
            own.append(n)          # unresolvable owners count as OWN: never hide a real name
    return own, inherited, sorted(set(bases))

def _render_members(cls, names):
    """`  name(sig)  — first doc line` for each name. The ONE renderer for member lists.

    Shared by the CLOSEST-ATTRIBUTES block and the own-API fallback below so the second one is
    not a drifting copy of the first.
    """
    lines = []
    for c in names:
        member = getattr(cls, c, None)
        try:
            sig = str(inspect.signature(member))
        except (TypeError, ValueError):
            sig = ""
        try:
            doc = (inspect.getdoc(member) or "").strip().splitlines()
        except Exception:
            doc = []
        # Bounded: fully-annotated signatures run to hundreds of characters, and this block
        # is injected into every retry prompt.
        line = "  " + c + (sig[:180] + "…)" if len(sig) > 180 else sig)
        lines.append(line + ("  — " + doc[0][:90] if doc else ""))
    return lines

def _render(cls, label, type_name, attr, max_names=60):
    """The report body, once the class and the absence are both established.

    Split out so the pre-execution entry point (`_callable_class_report`, which proves the class
    a different way) emits the SAME text rather than a second, drifting copy of it.
    """
    root = cls.__module__.split(".")[0]
    ver = None
    try:
        ver = getattr(importlib.import_module(root), "__version__", None)
    except Exception:
        pass
    if not isinstance(ver, str):
        try:
            from importlib.metadata import version as _pkgver
            ver = _pkgver(root)
        except Exception:
            ver = None

    public = sorted(n for n in dir(cls) if not n.startswith("_"))
    own, inherited, builtin_bases = _split_own(cls, public)
    out = [f"CLASS {label}  (defined in {cls.__module__}; {root} version={ver!r})",
           f"ABSENT: {type_name}.{attr} does NOT exist on this class in the INSTALLED version."]

    symbol = attr      # so the matching block below stays character-identical to SNIPPET's
    close = difflib.get_close_matches(symbol, public, n=8, cutoff=0.5)
    # KEEP THIS BLOCK LITERALLY IDENTICAL to the one in SNIPPET's `_report`: the two
    # snippets are separate strings by design (each must be self-contained), so the only
    # protection against drift is that the text matches.
    # difflib alone finds RENAMES that look alike (sph_harm -> sph_harm_y) but misses
    # SEMANTIC renames that share no spelling (lpmn -> assoc_legendre_p). Add substring
    # and stem matches so the real replacement is surfaced, not buried in 365 names.
    low = symbol.lower().strip("_")
    stems = [low] + [low[:i] for i in range(len(low), 2, -1)][:4]
    # Truncating the WHOLE name never reaches a rename that shares only a leading WORD.
    # Measured in biopython 1.87: difflib.get_close_matches("CodonUsage", dir(Bio.SeqUtils),
    # cutoff=0.5) is [], and the truncation stems stop at `codonus`, which is not a
    # substring of `codonadaptationindex` — so the report said "No similarly-named symbol
    # found" while CodonAdaptationIndex sat in the very same module. Split the name into
    # words (underscores + camelCase) and prefix-match the leading ones instead.
    # 4-char floor: shorter leading words are generic verbs (get/set/add/to/is) that match
    # a large fraction of any module and would push the real candidate past the cap.
    words = [w.lower() for part in symbol.split("_")
             for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[^A-Z_]+", part)]
    heads = [w for w in words[:2] if len(w) >= 4]
    lows = [(n_, n_.lower()) for n_ in public]
    # Ordering is load-bearing in a prompt: difflib's hits keep the front, then the
    # leading-word matches (the ones that carry a SEMANTIC rename), then the weaker
    # substring stems. The cap truncates the tail, never a difflib hit.
    for n_, nl in lows:
        if n_ not in close and any(nl.startswith(h) for h in heads):
            close.append(n_)
    for n_, nl in lows:
        if n_ not in close and any(st and (st in nl or nl.startswith(st)) for st in stems):
            close.append(n_)
    close = close[:12]
    if close:
        out.append("CLOSEST EXISTING ATTRIBUTES (verify the signature AND the summary line "
                   "before substituting — a renamed method may take its arguments in a "
                   "DIFFERENT ORDER, or return a different quantity):")
        out += _render_members(cls, close)
    else:
        # With NO candidate, this branch used to emit a bare comma-separated dump of names — no
        # signatures, no doc lines — leaving the real replacement to be guessed among many
        # plausible-looking names. The right method is not always reachable from the missing
        # symbol by ANY spelling rule (difflib can sit far below cutoff, and short leading words
        # fall under the 4-char floor), and loosening the matcher to close that gap would only
        # buy false candidates. So when nothing matched, stop trying to rank and render the
        # class's OWN api in full instead: the real method's signature plus its summary line is
        # the decisive fact, and a bare name dump never showed it.
        out.append("No similarly-named attribute found.")
        if own:
            shown = own[:12]        # same cap as the candidate list — this goes in a prompt
            out.append("THIS CLASS'S OWN PUBLIC API (%d%s) — the replacement is most likely "
                       "here:" % (len(own),
                                  ", first %d shown" % len(shown) if len(own) > len(shown) else ""))
            out += _render_members(cls, shown)
        else:
            out.append("Public attributes are listed below.")
    # The replacement for a removed METHOD is often a module-level FUNCTION (pandas dropped
    # DataFrame.append in favour of pandas.concat), which no amount of introspecting the class
    # can surface. Say so, rather than implying the list below is exhaustive.
    out.append("NOTE: the replacement may not be a method at all — check the module's "
               "top-level functions too.")
    out.append("ALL PUBLIC ATTRIBUTES (%d):" % len(public))
    if inherited:
        # Still exhaustive, but no longer presenting `clear`/`get`/`keys` as if they were part of
        # this library's API. A class with no builtin base has an EMPTY `inherited` and keeps the
        # single-line form below unchanged.
        out.append("  defined by this class (%d): " % len(own)
                   + ", ".join(own[:max_names]) + (" ..." if len(own) > max_names else ""))
        out.append("  inherited from %s, NOT this library's API (%d): "
                   % ("/".join(builtin_bases) or "a builtin base", len(inherited))
                   + ", ".join(inherited[:max_names])
                   + (" ..." if len(inherited) > max_names else ""))
    else:
        out.append("  " + ", ".join(public[:max_names])
                   + (" ..." if len(public) > max_names else ""))
    return "\n".join(out)

def _resolve_dotted(path):
    """The object a dotted path names, or None — longest importable PREFIX, then getattr.

    `pkg.Container` (a class) is not a module, so import_module fails on the whole path; the split
    between "module part" and "attribute part" is not knowable from the text and is found by
    trying. Anything that does not resolve returns None: the caller then says nothing.
    """
    parts = [p for p in path.split(".") if p]
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:
            continue
        for p in parts[i:]:
            obj = getattr(obj, p, None)
            if obj is None:
                return None
        return obj
    return None

def _produced_class(obj):
    """The class a CALLABLE produces, when the environment PROVES it — else None.

    A class is its own answer. For a function the only evidence available without RUNNING it is
    the return annotation. Under `from __future__ import annotations` it is a STRING ("Container")
    (PEP 563), so a bare identifier is resolved against the function's own module. Anything
    else is refused rather than guessed — measured: pandas.read_csv is annotated
    "DataFrame | TextFileReader", two different classes, so no absence could be proven from it.
    """
    if inspect.isclass(obj):
        return obj
    if not callable(obj):
        return None
    try:
        ann = inspect.signature(obj).return_annotation
    except (TypeError, ValueError):
        return None
    if ann is inspect.Signature.empty:
        return None
    if inspect.isclass(ann):
        return ann
    if not isinstance(ann, str):
        return None
    # `isidentifier()` is exactly the "bare name" test: it rejects unions, subscripts, tuples
    # and whitespace, i.e. every annotation that names more than one possible class.
    name = ann.strip()
    if not name.isidentifier():
        return None
    resolved = getattr(sys.modules.get(getattr(obj, "__module__", "") or ""), name, None)
    return resolved if inspect.isclass(resolved) else None

def _synthesises_attributes(cls):
    """True when the class MAKES UP attributes at runtime, so class-level absence proves nothing.

    Measured: pandas.DataFrame defines __getattr__ (column access), so `hasattr(DataFrame, x)`
    is False for names a real frame answers happily — flagging those would be a false positive.
    A class that defines neither hook is judgeable.
    """
    for klass in getattr(cls, "__mro__", [cls]):
        if klass is object:      # every class inherits object's lookup; that is not synthesis
            continue
        if "__getattr__" in vars(klass):
            return True
        # __getattribute__ counts only when it is PYTHON code actually overriding lookup.
        # Measured: builtins expose a C slot wrapper for it (`'__getattribute__' in vars(dict)`
        # is True) that behaves exactly like object's, so treating mere presence as synthesis
        # refused every dict subclass — collections.Counter included.
        if inspect.isfunction(vars(klass).get("__getattribute__")):
            return True
    return False

def _loaded_subclasses(cls, limit=4000):
    """Every loaded subclass, transitively. Never raises; a hierarchy too large to finish is
    truncated (callers treat that as "cannot judge")."""
    seen, out, stack = set(), [], [cls]
    while stack and len(out) < limit:
        c = stack.pop()
        try:
            subs = c.__subclasses__()
        except Exception:
            return out + [None]
        for s in subs:
            if id(s) not in seen:
                seen.add(id(s))
                out.append(s)
                stack.append(s)
    return out + ([None] if len(out) >= limit else [])

def _callable_class_report(spec, max_names=60):
    """Report for an attribute provably absent from the class a CALLABLE produces, else "".

    The PRE-execution counterpart of `_class_report`: preflight reads `obj.method()` out of code
    that has not run, so there is no traceback naming the type — only the call that PRODUCED the
    object. `static_only` is the caller saying it has NO runtime proof, which is why a class that
    synthesises attributes has to be refused there; the retry-time path keeps its own answer,
    where a real AttributeError already proved the lookup failed.

    Two more refusals under `static_only`, both about the object being a SUBCLASS of what the
    callable names: any loaded subclass that has the attribute (the factory may return one),
    and — for a class the callable merely DECLARES rather than IS — a family of
    PLUGIN_HIERARCHY_MIN_SUBCLASSES or more loaded subclasses (a plugin point whose members
    register as they are used, so even "no loaded subclass has it" is not proof). The constant
    is `api_types.PLUGIN_HIERARCHY_MIN_SUBCLASSES`, substituted into this snippet below.
    """
    path, attr = spec.get("callable", ""), spec.get("attr", "")
    if not path or not attr:
        return ""
    obj = _resolve_dotted(path)
    if obj is None:
        return ""
    cls = _produced_class(obj)
    if cls is None:
        return ""
    if spec.get("static_only") and _synthesises_attributes(cls):
        return ""
    if hasattr(cls, attr):
        return ""
    if spec.get("static_only"):
        subs = _loaded_subclasses(cls)
        if any(s is None or hasattr(s, attr) for s in subs):
            return ""
        if not inspect.isclass(obj) and len(subs) >= PLUGIN_HIERARCHY_MIN_SUBCLASSES:
            return ""
    label = path if inspect.isclass(obj) else path + "() -> " + cls.__name__
    return _render(cls, label, cls.__name__, attr, max_names)

def _full_class_report(spec, max_names=60):
    """Full public-API dump (constructor + own methods) for a class the CALLER has already
    resolved as a candidate REPLACEMENT — no absence claim, no traceback.

    Unlike `_class_report`/`_callable_class_report`, this never says a specific attribute is
    missing, because it is used BEFORE any code exists to fail on one: it is the class-API
    expansion step of the bypass-after-fault guard's proactive nudge, which already knows a
    MODULE-level symbol is absent and wants to hand over its replacement CLASS'S own method
    inventory too, not just the constructor signature `module_shape_report` already prints — a
    constructor signature alone (e.g. `CodonAdaptationIndex(sequences, table=...)`) does not
    reveal the class's own method names, so guessing at plausible ones instead of the real
    method is a predictable failure mode this closes. Reuses `_resolve_dotted`, `_split_own`
    and `_render_members` — the SAME helpers `_render` is built from — rather than a second copy
    of the member-listing logic. `spec` = {"callable": "Bio.SeqUtils.CodonAdaptationIndex"}, a
    dotted path the caller has already resolved. "" when the path does not resolve or does not
    name a class (a function/constant replacement is skipped, not forced into a class-shaped
    report).
    """
    path = spec.get("callable", "")
    if not path:
        return ""
    obj = _resolve_dotted(path)
    if obj is None or not inspect.isclass(obj):
        return ""
    cls = obj
    root = cls.__module__.split(".")[0]
    ver = None
    try:
        ver = getattr(importlib.import_module(root), "__version__", None)
    except Exception:
        pass
    if not isinstance(ver, str):
        try:
            from importlib.metadata import version as _pkgver
            ver = _pkgver(root)
        except Exception:
            ver = None
    try:
        ctor_sig = str(inspect.signature(cls))
    except (TypeError, ValueError):
        ctor_sig = "(...)"
    public = sorted(n for n in dir(cls) if not n.startswith("_"))
    own, inherited, builtin_bases = _split_own(cls, public)
    out = [f"CLASS {path}  (defined in {cls.__module__}; {root} version={ver!r})",
           f"CONSTRUCTOR: {cls.__name__}{ctor_sig}"]
    if own:
        shown = own[:max_names]
        out.append("THIS CLASS'S OWN PUBLIC API (%d%s):" % (
            len(own), ", first %d shown" % len(shown) if len(own) > len(shown) else ""))
        out += _render_members(cls, shown)
    if inherited:
        out.append("inherited from %s, NOT this library's API (%d): "
                   % ("/".join(builtin_bases) or "a builtin base", len(inherited))
                   + ", ".join(inherited[:max_names])
                   + (" ..." if len(inherited) > max_names else ""))
    return "\n".join(out)

if __name__ == "__main__":
    # One `python -c CLASS_SNIPPET <json>` invocation serves BOTH report kinds — the eval
    # harness shells out to a pinned interpreter this way, and a second entry point would mean
    # a second seam to keep in step. The `callable` key is what distinguishes the specs.
    _spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(_callable_class_report(_spec) if _spec.get("callable") else _class_report(_spec))
'''


# One constant for both tiers: the snippet is self-contained text executed inside the target
# interpreter, so the value is substituted in rather than imported there.
from cmbagent_lg.self_debug.api_types import PLUGIN_HIERARCHY_MIN_SUBCLASSES as _PLUGIN_MIN  # noqa: E402

CLASS_SNIPPET = CLASS_SNIPPET.replace("PLUGIN_HIERARCHY_MIN_SUBCLASSES", str(int(_PLUGIN_MIN)))


def introspect_class_local(spec: dict) -> str:
    """Run the class report against THIS process's environment. "" when nothing is provable.

    The seam a caller rebinds to answer from a DIFFERENT interpreter (or shells out to
    `[python, "-c", CLASS_SNIPPET, json.dumps(spec)]`).
    """
    ns: dict = {}
    exec(compile(CLASS_SNIPPET, "<api_introspect_class>", "exec"), ns)
    try:
        return ns["_class_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] class report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


def introspect_callable_class_local(spec: dict) -> str:
    """Report for an attribute provably absent from the class a CALLABLE produces, else "".

    For the PRE-execution caller (`api_preflight`), which cannot use `introspect_class_local`:
    with no traceback, only the call that produced `obj` is knowable. `spec` =
    {"callable": "anndata.read_h5ad", "attr": "concatenate", "static_only": True}. Same seam and
    contract as `introspect_class_local`: answers from the installed package, never raises.
    """
    ns: dict = {}
    exec(compile(CLASS_SNIPPET, "<api_introspect_class>", "exec"), ns)
    try:
        return ns["_callable_class_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] callable class report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


def introspect_full_class_local(spec: dict) -> str:
    """Full public-API dump for a class the caller has already resolved as a candidate
    replacement — no absence claim, no traceback required. See `_full_class_report` (in
    `CLASS_SNIPPET`) for the shape of `spec`. "" when nothing resolves or the object is not a
    class. Same seam and contract as `introspect_callable_class_local`.
    """
    ns: dict = {}
    exec(compile(CLASS_SNIPPET, "<api_introspect_class>", "exec"), ns)
    try:
        return ns["_full_class_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] full class report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


# Resolution + report for the CALL shape: a real callable, given a keyword or positional count
# its own RESOLVED signature does not accept. Self-contained for the same reason as SNIPPET /
# CLASS_SNIPPET — executed INSIDE the environment being asked about.
#
# `spec` has two shapes, sharing one contract ("prove or say nothing"):
#   * pre-execution (`api_preflight`): {"callable", "kwargs", "n_positional", "has_starargs"} —
#     nothing has failed yet, so absence must be PROVEN from the signature alone;
#   * retry-time (`retry_report`, below): {"callable", "already_failed": True, "invalid_kwarg"}
#     — a TypeError already proved SOME mismatch, so this renders the real signature rather than
#     re-proving it, and stays silent if the RESOLVED signature disagrees with the crash (a sign
#     the resolution found the wrong callable, e.g. a same-named function in another module).
SIGNATURE_SNIPPET = r'''
import importlib, inspect, json, sys

def _resolve_dotted(path):
    """The object a dotted path names, or None — longest importable PREFIX, then getattr.

    Identical in shape to CLASS_SNIPPET's own `_resolve_dotted`; duplicated rather than shared
    because each snippet must stay self-contained (it may be shipped to a remote interpreter as
    plain text).
    """
    parts = [p for p in path.split(".") if p]
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:
            continue
        for p in parts[i:]:
            obj = getattr(obj, p, None)
            if obj is None:
                return None
        return obj
    return None

def _param_names(sig):
    return [p.name for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.KEYWORD_ONLY)]

def _max_positional(sig):
    """Count of positional slots, or None when `*args` makes it unbounded."""
    n = 0
    for p in sig.parameters.values():
        if p.kind == inspect.Parameter.VAR_POSITIONAL:
            return None
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            n += 1
    return n

def _has_var_keyword(sig):
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

def _render(path, obj, sig, reason):
    # A class renders as its constructor, so the report shares the traceback's vocabulary
    # (`Thing.__init__() got an unexpected keyword argument`).
    head = f"CALLABLE {path}{sig}"
    if inspect.isclass(obj):
        head += f"  (constructor {obj.__name__}.__init__)"
    lines = [head]
    try:
        doc = (inspect.getdoc(obj) or "").strip().splitlines()
    except Exception:
        doc = []
    if doc:
        lines.append("DOC (first lines):")
        lines += ["  " + l for l in doc[:6]]
    lines.append(reason)
    names = _param_names(sig)
    lines.append("VALID PARAMETER NAMES (%d): %s" % (len(names), ", ".join(names)))
    return "\n".join(lines)

def _resolve_class(type_name, modules, froms):
    """`(label, class)` for the ONE class named `type_name` the script's own imports reach, or
    None. Same rule as CLASS_SNIPPET's `_resolve` (duplicated: each snippet is self-contained),
    plus one fallback: a class a package does not export at top level (its factory does) is
    looked up in the already-imported submodules of the script's own top-level packages, by
    identity and `__module__`, with the same uniqueness rule."""
    found = {}
    for module_name, attr in [(m, type_name) for m in modules] + [tuple(f) for f in froms]:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue
        obj = getattr(mod, attr, None)
        if inspect.isclass(obj) and obj.__name__ == type_name:
            found.setdefault(id(obj), (module_name + "." + attr, obj))
    if not found:
        roots = {m.split(".")[0] for m in modules} | {f[0].split(".")[0] for f in froms}
        for name, mod in list(sys.modules.items()):
            if mod is None or name.split(".")[0] not in roots:
                continue
            try:
                obj = vars(mod).get(type_name)
            except Exception:
                continue
            if (inspect.isclass(obj) and obj.__name__ == type_name
                    and getattr(obj, "__module__", None) == name):
                found.setdefault(id(obj), (name + "." + type_name, obj))
    return list(found.values())[0] if len(found) == 1 else None

def _resolve_method(spec):
    """`(label, function, signature_without_self)` for `type.method`, or None.

    The class name is CPython's own (`ModelParams.set_values() got an unexpected keyword
    argument`); it names the class that DEFINES the method. The resolved method must agree:
    its `__qualname__` ends in `type.method`, so a same-named class or an override elsewhere
    in the hierarchy stays silent."""
    resolved = _resolve_class(spec.get("type", ""), spec.get("modules", []),
                              spec.get("froms", []))
    if resolved is None:
        return None
    label, cls = resolved
    name = spec.get("method", "")
    try:
        raw = inspect.getattr_static(cls, name)
    except AttributeError:
        return None
    static = isinstance(raw, staticmethod)
    fn = raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
    qual = getattr(fn, "__qualname__", "") or ""
    if qual.split(".")[-2:] != [spec.get("type", ""), name] or not callable(fn):
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    params = list(sig.parameters.values())
    if not static and params and params[0].kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                    inspect.Parameter.POSITIONAL_OR_KEYWORD):
        sig = sig.replace(parameters=params[1:])
    return f"{label}.{name}", fn, sig

def _qual_agrees(obj, qualname):
    """True when `obj` is the callable a qualified message names: a function whose
    `__qualname__` ends in it, or a class whose `__init__` does."""
    want = qualname.split(".")[-2:]
    fn = getattr(obj, "__init__", None) if inspect.isclass(obj) else obj
    qual = getattr(fn, "__qualname__", "") or ""
    return qual.split(".")[-2:] == want

def _signature_report(spec):
    """Report for a call PROVEN to conflict with the RESOLVED signature, else "" — including
    when the signature accepts `**kwargs`, which means nothing can be proven at this boundary (a
    permissive signature says any keyword MAY be valid)."""
    if spec.get("type"):
        if not spec.get("already_failed"):
            return ""
        resolved = _resolve_method(spec)
        if resolved is None:
            return ""
        path, obj, sig = resolved
    else:
        path = spec.get("callable", "")
        if not path:
            return ""
        obj = _resolve_dotted(path)
        if obj is None or not callable(obj):
            return ""
        if spec.get("qualname") and not _qual_agrees(obj, spec["qualname"]):
            return ""
        try:
            sig = inspect.signature(obj)
        except (TypeError, ValueError):
            return ""

    missing = spec.get("missing") or []
    if spec.get("already_failed") and missing:
        # Python's call binding raises "missing N required argument(s)" whatever `*args` or
        # `**kwargs` the signature has, so the agreement check is on the names themselves:
        # each must be a parameter of the RESOLVED signature with no default.
        for n in missing:
            p = sig.parameters.get(n)
            if (p is None or p.default is not inspect.Parameter.empty
                    or p.kind in (inspect.Parameter.VAR_POSITIONAL,
                                  inspect.Parameter.VAR_KEYWORD)):
                return ""
        return _render(path, obj, sig,
                       "MISSING: required parameter(s) %s not given — the INSTALLED signature "
                       "has no default for them." % ", ".join(repr(n) for n in missing))

    if spec.get("already_failed"):
        # Retry-time: a TypeError already proved a keyword/arity mismatch against WHATEVER
        # callable actually raised it. `**kwargs` on the RESOLVED signature is proof the
        # resolution landed on the wrong callable (Python's call binding never raises this shape
        # for one) — stay silent rather than report something that disagrees with the crash.
        if _has_var_keyword(sig):
            return ""
        bad_kwarg = spec.get("invalid_kwarg") or None
        if bad_kwarg and bad_kwarg in sig.parameters:
            return ""  # disagrees with the exception — likely resolved the wrong callable
        reason = (f"INVALID: '{bad_kwarg}' is not a parameter of the INSTALLED signature."
                  if bad_kwarg else
                  "INVALID: the call's arguments do not match the INSTALLED signature above "
                  "(see the traceback for the exact counts).")
        return _render(path, obj, sig, reason)

    # Pre-execution: nothing has failed yet, so a mismatch must be PROVEN from the signature.
    if _has_var_keyword(sig):
        return ""  # **kwargs pass-through: cannot prove any keyword invalid at this boundary
    kwargs = spec.get("kwargs") or []
    unknown = [k for k in kwargs if k not in sig.parameters]
    if unknown:
        return _render(path, obj, sig,
                       "INVALID: keyword(s) not in the INSTALLED signature: "
                       + ", ".join(unknown))
    if not spec.get("has_starargs"):
        n_pos = spec.get("n_positional")
        max_pos = _max_positional(sig)
        if n_pos is not None and max_pos is not None and n_pos > max_pos:
            return _render(path, obj, sig,
                           "INVALID: %d positional argument(s) given, the INSTALLED signature "
                           "takes at most %d." % (n_pos, max_pos))
    return ""

if __name__ == "__main__":
    _spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(_signature_report(_spec))
'''


def introspect_call_signature_local(spec: dict) -> str:
    """Run the call-shape report against THIS process's environment. "" when nothing is provable.

    Same seam and contract as `introspect_callable_class_local` — a caller redirects it at a
    different interpreter by rebinding this attribute, or by shelling out to
    `[python, "-c", SIGNATURE_SNIPPET, json.dumps(spec)]`.
    """
    ns: dict = {}
    exec(compile(SIGNATURE_SNIPPET, "<api_introspect_signature>", "exec"), ns)
    try:
        return ns["_signature_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] signature report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


# ── ARTIST_SNIPPET (artist_kwarg shape) ──────────────────────────────────────
#
# Answers `classify_api_error`'s `"artist_kwarg"` kind: matplotlib's `Artist.set(**kwargs)`
# rejecting a kwarg deep inside a plotting call whose OWN signature ends in `**kwargs` and is
# therefore permanently unprovable at ITS boundary (see SIGNATURE_SNIPPET and `api_preflight`).
#
# The class is NOT resolved from the failing script's imports (unlike `_resolve` in
# CLASS_SNIPPET): plotting code essentially never imports matplotlib Artist classes by name.
# Instead this searches matplotlib's OWN closed set of `Artist` subclasses, reached by importing
# a fixed list of ordinary submodules. That list deliberately excludes `mpl_toolkits`: importing
# `mplot3d.axis3d` duplicates `Axis`/`XAxis`/`YAxis` and would make them ambiguous. Ambiguity is
# always refused rather than guessed at, so an unrelated import of it elsewhere stays safe.
ARTIST_SNIPPET = r'''
import importlib, inspect, json, sys

def _artist_subclasses():
    """Every `matplotlib.artist.Artist` subclass reachable from a fixed set of ordinary
    submodules — the classes actually involved in `Artist.set()` AttributeErrors live in one of
    these; a module that fails to import (e.g. an optional mpl_toolkits backend) is skipped, not
    fatal."""
    import matplotlib.artist as _art
    for name in ("matplotlib.collections", "matplotlib.lines", "matplotlib.patches",
                 "matplotlib.text", "matplotlib.image", "matplotlib.legend",
                 "matplotlib.container", "matplotlib.quiver", "matplotlib.contour",
                 "matplotlib.colorbar", "matplotlib.axes", "matplotlib.figure",
                 "matplotlib.spines", "matplotlib.axis", "matplotlib.table",
                 "matplotlib.streamplot"):
        try:
            importlib.import_module(name)
        except Exception:
            continue
    seen, out, stack = set(), [], [_art.Artist]
    while stack:
        c = stack.pop()
        for s in c.__subclasses__():
            if id(s) not in seen:
                seen.add(id(s))
                out.append(s)
                stack.append(s)
    return out

def _resolve_artist_class(cls_name):
    """The ONE Artist subclass named `cls_name`, or None if zero or MORE THAN ONE distinct
    class share that name (ambiguous ⇒ refuse, never guess)."""
    matches = {}
    for c in _artist_subclasses():
        if c.__name__ == cls_name:
            matches[id(c)] = c
    return list(matches.values())[0] if len(matches) == 1 else None

def _artist_kwarg_report(spec, max_names=60):
    """Report for a kwarg PROVEN absent as a settable Artist property, else "" (unresolvable
    class, ambiguous class, or the crash disagrees with a fresh check — never a guess)."""
    cls_name, kwarg = spec.get("cls_name", ""), spec.get("kwarg", "")
    if not cls_name or not kwarg:
        return ""
    cls = _resolve_artist_class(cls_name)
    if cls is None:
        return ""
    setter = getattr(cls, f"set_{kwarg}", None)
    if callable(setter):
        # Disagrees with the exception that supposedly produced this spec (e.g. an
        # instance-level attribute masking the class) — say nothing rather than something that
        # contradicts the crash.
        return ""

    try:
        import matplotlib
        ver = getattr(matplotlib, "__version__", None)
    except Exception:
        ver = None
    setters = sorted(n[4:] for n in dir(cls)
                      if n.startswith("set_") and n != "set_" and callable(getattr(cls, n, None)))
    out = [
        f"CLASS {cls.__module__}.{cls.__name__}  (matplotlib version={ver!r})",
        f"ABSENT: {cls_name}.set_{kwarg}() does not exist on this class in the INSTALLED "
        f"matplotlib.",
        f"WHY THIS IS PROVEN, NOT A GUESS: matplotlib's own Artist.set(**kwargs) "
        f"(matplotlib.artist.Artist._update_props) accepts a keyword ONLY if a callable "
        f"'set_<name>' exists on the instance; '{kwarg}' has none here — this is the exact "
        f"check that raised the AttributeError above, re-run against the installed class.",
        f"VALID SETTABLE PROPERTIES on {cls_name} ({len(setters)}): "
        + ", ".join(setters[:max_names]) + (" ..." if len(setters) > max_names else ""),
    ]
    # Informational only, and entirely generic (not hardcoded to any one kwarg name): if the
    # SAME kwarg name the caller invented is a real, explicitly-named parameter one layer
    # further into matplotlib's own SAVE path, say where it actually belongs. Proven via
    # `inspect.signature`, not asserted — `Figure.savefig` itself is `(self, fname, *,
    # transparent=None, **kwargs)` in matplotlib 3.11.1 (checked live: 'dpi' is NOT a named
    # parameter there either — savefig forwards through **kwargs same as any other
    # KWARGS_PASSTHROUGH boundary), but `FigureCanvasBase.print_figure`, which savefig calls
    # into, DOES name 'dpi' explicitly — checked live, not assumed.
    try:
        from matplotlib.backend_bases import FigureCanvasBase
        save_sig = inspect.signature(FigureCanvasBase.print_figure)
        if kwarg in save_sig.parameters:
            out.append(
                f"NOTE: '{kwarg}' IS a real, named parameter — but of matplotlib's SAVE path "
                f"(FigureCanvasBase.print_figure, reached via Figure.savefig(..., {kwarg}=...)), "
                f"not of this Artist. If the intent was output resolution, it belongs on the "
                f"figure's save call (e.g. `fig.savefig(path, {kwarg}=...)`), not on this plot "
                f"call's own kwargs."
            )
    except Exception:
        pass
    return "\n".join(out)

if __name__ == "__main__":
    _spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(_artist_kwarg_report(_spec))
'''


def introspect_artist_kwarg_local(spec: dict) -> str:
    """Run the ARTIST_SET report against THIS process's environment. "" when nothing is provable.

    Same seam and contract as `introspect_call_signature_local`.
    """
    ns: dict = {}
    exec(compile(ARTIST_SNIPPET, "<api_introspect_artist>", "exec"), ns)
    try:
        return ns["_artist_kwarg_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] artist-kwarg report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


# ── SHAPE_SNIPPET (shapes that name no function) ─────────────────────────────
#
# Answers the `classify_api_error` kinds whose message names no callable, or only a type:
# `property_setter`, `not_callable`, `ctypes_arity`, `value_type` and `unknown_argument`. The
# engine reads the failing script line and the traceback frames (`_traceback_frames`, pure
# text), and this snippet, executed INSIDE the environment the code ran in like the others,
# resolves what they name and checks that it agrees with the message before saying anything:
#   * property_setter — the class from the script's imports, and the attribute IS a property
#     with no setter on it;
#   * not_callable / ctypes_arity — the attribute called on the failing line is, on exactly ONE
#     class the script's imports export, a non-callable data attribute / a ctypes wrapper whose
#     `argtypes` count equals the count in the message;
#   * value_type / unknown_argument — the library callable the failing line calls (a module
#     alias call, resolved engine-side) and the innermost library frame's function, found by
#     its file AND line range, never by name alone.
# Anything else, including more than one candidate, yields "".
SHAPE_SNIPPET = r'''
import difflib, importlib, inspect, json, re, sys

def _resolve(type_name, modules, froms):
    found = {}
    for module_name, attr in [(m, type_name) for m in modules] + [tuple(f) for f in froms]:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            continue
        obj = getattr(mod, attr, None)
        if inspect.isclass(obj) and obj.__name__ == type_name:
            found.setdefault(id(obj), (module_name + "." + attr, obj))
    return list(found.values())[0] if len(found) == 1 else None

def _resolve_dotted(path):
    parts = [p for p in path.split(".") if p]
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:
            continue
        for p in parts[i:]:
            obj = getattr(obj, p, None)
            if obj is None:
                return None
        return obj
    return None

def _version(cls_or_fn):
    root = (getattr(cls_or_fn, "__module__", "") or "").split(".")[0]
    ver = None
    try:
        ver = getattr(importlib.import_module(root), "__version__", None)
    except Exception:
        pass
    if not isinstance(ver, str):
        try:
            from importlib.metadata import version as _pkgver
            ver = _pkgver(root)
        except Exception:
            ver = None
    return root, ver

def _doc(obj, n=6):
    try:
        return ["  " + l for l in (inspect.getdoc(obj) or "").strip().splitlines()[:n]]
    except Exception:
        return []

def _sig(obj, cap=400):
    try:
        s = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"
    return s if len(s) <= cap else s[:cap] + "…)"

def _params(obj, skip_self=False):
    try:
        ps = list(inspect.signature(obj).parameters.values())
    except (TypeError, ValueError):
        return []
    if skip_self and ps and ps[0].name in ("self", "cls"):
        ps = ps[1:]
    return [p for p in ps if p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                            inspect.Parameter.VAR_KEYWORD)]

def _exported_classes(modules, froms):
    seen = {}
    for m in modules:
        try:
            mod = importlib.import_module(m)
        except Exception:
            continue
        try:
            vals = list(vars(mod).values())
        except Exception:
            continue
        for v in vals:
            if inspect.isclass(v):
                seen.setdefault(id(v), v)
    for m, n in froms:
        try:
            v = getattr(importlib.import_module(m), n, None)
        except Exception:
            continue
        if inspect.isclass(v):
            seen.setdefault(id(v), v)
    return list(seen.values())

def _definer(cls, attr):
    for klass in getattr(cls, "__mro__", [cls]):
        try:
            if attr in vars(klass):
                return klass
        except Exception:
            return None
    return None

def _candidates(attrs, spec, pred):
    """`[(attr, defining_class, raw)]`, one per DEFINING class, over the classes the script's
    imports export, for attributes satisfying `pred(raw)`."""
    out = {}
    for cls in _exported_classes(spec.get("modules", []), spec.get("froms", [])):
        for a in attrs:
            try:
                raw = inspect.getattr_static(cls, a)
            except AttributeError:
                continue
            except Exception:
                continue
            if not pred(raw):
                continue
            d = _definer(cls, a) or cls
            out.setdefault((a, id(d)), (a, d, raw))
    return list(out.values())

def _words(name):
    ws = [w.lower() for part in name.split("_")
          for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[^A-Z_]+", part)]
    return [w for w in ws if len(w) >= 4]

def _related_params(cls, attr, skip=()):
    """Public methods of `cls` with a parameter sharing a 4+-letter word with `attr`, as
    `name: p=default, ...` lines (at most 5). Facts about real signatures, nothing ranked."""
    words = _words(attr)
    if not words:
        return []
    lines = []
    for n in sorted(dir(cls)):
        if n.startswith("_") or n.startswith("f_") or n in skip:
            continue
        fn = getattr(cls, n, None)
        if not callable(fn) or inspect.isclass(fn):
            continue
        hits = [p for p in _params(fn, skip_self=True)
                if any(w in p.name.lower() for w in words)]
        if hits:
            lines.append("  %s: %s" % (n, ", ".join(
                p.name + ("" if p.default is inspect.Parameter.empty else "=" + repr(p.default)[:30])
                for p in hits[:6])))
        if len(lines) >= 5:
            break
    return lines

def _setter_names(cls):
    return sorted(n for n in dir(cls) if n.startswith("set") and not n.startswith("_")
                  and callable(getattr(cls, n, None)))[:30]

def _property_setter_report(spec):
    resolved = _resolve(spec.get("type", ""), spec.get("modules", []), spec.get("froms", []))
    if resolved is None:
        return ""
    label, cls = resolved
    attr = spec.get("attr", "")
    try:
        raw = inspect.getattr_static(cls, attr)
    except AttributeError:
        return ""
    if not isinstance(raw, property) or raw.fset is not None:
        return ""   # disagrees with the message: say nothing
    root, ver = _version(cls)
    out = [f"CLASS {label}  (defined in {cls.__module__}; {root} version={ver!r})",
           f"READ-ONLY: {cls.__name__}.{attr} is a property with NO SETTER in the INSTALLED "
           f"version — it is computed, and cannot be assigned."]
    doc = _doc(raw.fget)
    if doc:
        out += ["GETTER DOC (first lines):"] + doc
    rel = _related_params(cls, attr)
    if rel:
        out += [f"PUBLIC METHODS OF {cls.__name__} WITH A PARAMETER SHARING A WORD WITH '{attr}':"] + rel
    setters = _setter_names(cls)
    if setters:
        out.append(f"SETTER METHODS OF {cls.__name__} ({len(setters)}): " + ", ".join(setters))
    return "\n".join(out)

def _is_value_attr(raw):
    # A data attribute read as a value: a property, a C-level getset/member descriptor, or a
    # ctypes Structure field. Never a function, a class or a callable object.
    if inspect.isfunction(raw) or inspect.isclass(raw) or isinstance(raw, (staticmethod, classmethod)):
        return False
    return isinstance(raw, property) or inspect.isgetsetdescriptor(raw) \
        or inspect.ismemberdescriptor(raw) or (inspect.isdatadescriptor(raw) and not callable(raw))

def _not_callable_report(spec):
    hits = _candidates(spec.get("attrs", []), spec, _is_value_attr)
    if len(hits) != 1:
        return ""
    attr, cls, raw = hits[0]
    root, ver = _version(cls)
    out = [f"CLASS {cls.__module__}.{cls.__name__}  ({root} version={ver!r})",
           f"NOT A METHOD: {cls.__name__}.{attr} is a data attribute"
           + (" (a property)" if isinstance(raw, property) else "")
           + f" in the INSTALLED version. Reading it gives the value itself (here a "
           f"{spec.get('type', 'value')}); read it as `obj.{attr}`, without parentheses."]
    doc = _doc(raw.fget if isinstance(raw, property) else raw)
    if doc:
        out += ["DOC (first lines):"] + doc
    return "\n".join(out)

def _ctypes_argtypes(raw):
    """The `argtypes` of the ctypes foreign function a Python wrapper closes over, or None."""
    try:
        import ctypes
        base = getattr(ctypes, "_CFuncPtr", None)
    except Exception:
        return None
    if base is None:
        return None
    fn = raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
    found = []
    for cell in (getattr(fn, "__closure__", None) or ()):
        try:
            v = cell.cell_contents
        except Exception:
            continue
        at = getattr(v, "argtypes", None) if isinstance(v, base) else None
        if isinstance(at, (list, tuple)):
            found.append(list(at))
    return found[0] if len(found) == 1 else None

def _ctypes_arity_report(spec):
    n = int(spec.get("n", -1))
    hits = _candidates(spec.get("attrs", []), spec,
                       lambda raw: (lambda at: at is not None and len(at) == n)(_ctypes_argtypes(raw)))
    if len(hits) != 1:
        return ""
    attr, cls, raw = hits[0]
    root, ver = _version(cls)
    at = _ctypes_argtypes(raw)
    names = [getattr(t, "__name__", str(t)) for t in at]
    out = [f"CLASS {cls.__module__}.{cls.__name__}  ({root} version={ver!r})",
           f"COMPILED FUNCTION: {cls.__name__}.{attr} is a thin wrapper around a compiled "
           f"(ctypes) function that takes exactly {n} arguments, the object itself first: "
           + ", ".join(names),
           f"The call passed {spec.get('m', '?')} (counting the object). Its Python signature "
           f"`{attr}{_sig(raw)}` does not show this."]
    doc = _doc(raw)
    if doc:
        out += ["DOC (first lines):"] + doc
    rel = _related_params(cls, attr)
    if rel:
        out += [f"PUBLIC METHODS OF {cls.__name__} WITH A PARAMETER SHARING A WORD WITH "
                f"'{attr}' (the usual Python-level way to set the same thing):"] + rel
    return "\n".join(out)

def _frame_function(tail, line, func, roots):
    """The function a traceback frame ran: found in the module its file names, and checked by
    file AND line range. `tail` is the path under site-packages (`pkg/mod.py`)."""
    if not tail.endswith(".py"):
        return None
    modname = tail[:-3].replace("/", ".").replace("\\", ".")
    if modname.endswith(".__init__"):
        modname = modname[: -len(".__init__")]
    if modname.split(".")[0] not in roots:
        return None
    try:
        mod = importlib.import_module(modname)
    except Exception:
        return None
    cands = []
    try:
        items = list(vars(mod).items())
    except Exception:
        return None
    for name, v in items:
        if name == func and inspect.isfunction(v):
            cands.append((f"{modname}.{name}", v))
        elif inspect.isclass(v) and getattr(v, "__module__", None) == modname:
            raw = vars(v).get(func)
            raw = raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
            if inspect.isfunction(raw):
                cands.append((f"{modname}.{v.__qualname__}.{func}", raw))
    out = {}
    for label, fn in cands:
        code = getattr(fn, "__code__", None)
        if code is None or not code.co_filename.replace("\\", "/").endswith(tail):
            continue
        try:
            lines = [l for _s, _e, l in code.co_lines() if l is not None]
        except Exception:
            lines = []
        if lines and min(lines + [code.co_firstlineno]) <= line <= max(lines):
            out.setdefault(id(fn), (label, fn))
    return list(out.values())[0] if len(out) == 1 else None

_ROLE_RE = re.compile(r":(?:meth|func):`~?(\.?[\w.]+)`")

def _forward_targets(callee, cap=10):
    """Callables a `**kwargs` forwarder's docstring names with Sphinx roles, resolved inside its
    own package. Facts read from the installed docstring, never inferred."""
    mod = getattr(callee, "__module__", "") or ""
    root = mod.split(".")[0]
    seen, out = set(), []
    for ref in _ROLE_RE.findall(inspect.getdoc(callee) or ""):
        for path in ([root + ref] if ref.startswith(".") else [ref, root + "." + ref]):
            obj = _resolve_dotted(path)
            if obj is not None and callable(obj):
                if id(obj) not in seen:
                    seen.add(id(obj))
                    out.append((path.lstrip("."), obj))
                break
        if len(out) >= cap:
            break
    return out

def _type_fields(type_name, root):
    """`(class_named_type_or_None, [(holder, field)])`: the ONE class named `type_name` in the
    already-imported modules of `root`, and the ctypes `_fields_` entries of that type."""
    found, holders = {}, []
    for name, mod in list(sys.modules.items()):
        if mod is None or name.split(".")[0] != root:
            continue
        try:
            vals = list(vars(mod).values())
        except Exception:
            continue
        for v in vals:
            if inspect.isclass(v) and v.__name__ == type_name:
                found.setdefault(id(v), v)
    cls = list(found.values())[0] if len(found) == 1 else None
    if cls is None:
        return None, []
    for name, mod in list(sys.modules.items()):
        if mod is None or name.split(".")[0] != root:
            continue
        try:
            vals = list(vars(mod).values())
        except Exception:
            continue
        for v in vals:
            if not inspect.isclass(v):
                continue
            try:
                fields = vars(v).get("_fields_") or []
            except Exception:
                continue
            for f in fields:
                if isinstance(f, (list, tuple)) and len(f) >= 2 and f[1] is cls:
                    holders.append((v, f[0]))
    uniq = {}
    for v, f in holders:
        uniq.setdefault((id(v), f), (v, f))
    return cls, list(uniq.values())[:4]

def _frame_report(spec):
    roots = set(spec.get("roots", []))
    callee_path = spec.get("callee") or ""
    callee = _resolve_dotted(callee_path) if callee_path else None
    if callee is not None and not callable(callee):
        callee = None
    inner = None
    if spec.get("inner"):
        tail, line, func = spec["inner"]
        inner = _frame_function(tail, int(line), func, roots)
    if callee is None and inner is None:
        return ""
    kw = spec.get("kw") or ""
    if kw:
        # Agreement: a callable that NAMES the keyword cannot be the one that rejected it.
        for obj in [callee] + ([inner[1]] if inner else []):
            if obj is not None and kw in [p.name for p in _params(obj)]:
                return ""
    out = []
    anchor = callee if callee is not None else inner[1]
    root, ver = _version(anchor)
    if callee is not None:
        out.append(f"CALL ON THE FAILING LINE: {callee_path}{_sig(callee)}  ({root} version={ver!r})")
        out += (["DOC (first lines):"] + _doc(callee)) if _doc(callee) else []
    if inner is not None and (callee is None or inner[1] is not getattr(callee, "__func__", callee)):
        out.append(f"RAISED INSIDE: {inner[0]}{_sig(inner[1])}")
        if callee is None:
            out += (["DOC (first lines):"] + _doc(inner[1])) if _doc(inner[1]) else []
    keywords = spec.get("call_keywords") or []
    if spec.get("shape") == "unknown_argument" and kw:
        out.append(f"UNKNOWN PARAMETER: '{kw}' is not accepted by the INSTALLED version.")
        pool = {}
        try:
            forwards = callee is not None and any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in inspect.signature(callee).parameters.values())
        except (TypeError, ValueError):
            forwards = False
        targets = _forward_targets(callee) if forwards else []
        if targets:
            out.append("THE FUNCTIONS ITS DOCSTRING NAMES (where it may forward keywords), with "
                       "their parameter names:")
            for path, obj in targets:
                names = [p.name for p in _params(obj, skip_self=True)]
                for n in names:
                    pool.setdefault(n, path)
                line_ = "  " + path + ": " + ", ".join(names)
                out.append(line_ if len(line_) <= 240 else line_[:237] + "...")
        for obj in [callee] + ([inner[1]] if inner else []):
            if obj is not None:
                for p in _params(obj, skip_self=True):
                    pool.setdefault(p.name, "")
        close = difflib.get_close_matches(kw, list(pool), n=5, cutoff=0.6)
        low = {n.lower(): n for n in pool}
        if kw.lower() in low and low[kw.lower()] not in close:
            close.insert(0, low[kw.lower()])
        if close:
            out.append("CLOSEST ACCEPTED PARAMETER NAMES: " + ", ".join(
                c + (f" ({pool[c]})" if pool[c] else "") for c in close))
    elif spec.get("shape") == "value_type":
        exp, got = spec.get("expected") or "", spec.get("got") or ""
        if exp == "real number":
            out.append(f"WRONG VALUE TYPE: a parameter here needs a real number (a float); a "
                       f"value of type {got} was given.")
            if inner is not None and keywords:
                ps = {p.name: p for p in _params(inner[1], skip_self=True)}
                shared = [k for k in keywords if k in ps]
                if shared:
                    out.append("KEYWORDS OF THE FAILING CALL THAT ARE PARAMETERS OF "
                               f"{inner[0].rsplit('.', 1)[-1]} (default shown): " + ", ".join(
                                   k + ("" if ps[k].default is inspect.Parameter.empty
                                        else "=" + repr(ps[k].default)[:30]) for k in shared[:12]))
        elif exp:
            cls, holders = _type_fields(exp, root)
            if cls is None:
                out.append(f"WRONG VALUE TYPE: an {exp} instance is required; a value of type "
                           f"{got} was given.")
                return "\n".join(out)
            out.append(f"WRONG VALUE TYPE: {exp} (class {cls.__module__}.{cls.__qualname__}) is "
                       f"required; a value of type {got} was given.")
            for holder, field in holders:
                note = (f"  {holder.__name__}.{field} holds an {exp} instance"
                        + (f" — the failing call passes '{field}=', which sets it" if field in keywords
                           else ""))
                out.append(note)
            try:
                members = [f[0] for f in (vars(cls).get("_fields_") or [])
                           if isinstance(f, (list, tuple)) and f and not str(f[0]).startswith("_")]
            except Exception:
                members = []
            if not members:
                members = [n for n in dir(cls) if not n.startswith("_")][:30]
            if members:
                out.append(f"{exp} MEMBERS ({len(members)}): " + ", ".join(members[:30])
                           + (" ..." if len(members) > 30 else ""))
    return "\n".join(out)

def _shape_report(spec):
    kind = spec.get("shape", "")
    if kind == "property_setter":
        return _property_setter_report(spec)
    if kind == "not_callable":
        return _not_callable_report(spec)
    if kind == "ctypes_arity":
        return _ctypes_arity_report(spec)
    if kind in ("value_type", "unknown_argument"):
        return _frame_report(spec)
    return ""

if __name__ == "__main__":
    _spec = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(_shape_report(_spec))
'''


def introspect_shape_local(spec: dict) -> str:
    """Run the SHAPE_SNIPPET report against THIS process's environment. "" when nothing is
    provable. Same seam and contract as `introspect_call_signature_local`."""
    ns: dict = {}
    exec(compile(SHAPE_SNIPPET, "<api_introspect_shape>", "exec"), ns)
    try:
        return ns["_shape_report"](spec)
    except Exception as e:  # noqa: BLE001 — a tool must never raise into the agent loop
        print(f"[api_introspect] shape report failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


# Traceback frames, read as text. A SCRIPT frame is one whose shown source line is the script's
# own line at that number and whose file is not under site-/dist-packages; that is checkable
# without knowing where the script was written or under which name it ran.
_FRAME_RE = re.compile(r'^  File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<func>[^\n]+)\n'
                       r'(?:    (?P<src>[^\n]*)\n)?', re.M)
_LIB_DIR_RE = re.compile(r"[/\\](?:site|dist)-packages[/\\]")


def _traceback_frames(stderr: str | None, code: str | None):
    """`(script_line, library_frame)` for the LAST traceback in `stderr`: the line number of the
    innermost frame that ran the script's own code, and `(path_under_site_packages, line, func)`
    of the innermost frame when it is library code below that script frame. Either may be None."""
    text = stderr or ""
    start = text.rfind("Traceback (most recent call last):")
    if start < 0 or not code:
        return None, None
    lines = code.splitlines()
    frames = [(m["path"], int(m["line"]), m["func"].strip(), (m["src"] or "").strip())
              for m in _FRAME_RE.finditer(text, start)]
    script_idx = None
    for i, (path, ln, _f, src) in enumerate(frames):
        if (src and not _LIB_DIR_RE.search(path) and 0 < ln <= len(lines)
                and lines[ln - 1].strip() == src):
            script_idx = i
    if script_idx is None:
        return None, None
    lib = None
    path, ln, func, _src = frames[-1]
    if len(frames) - 1 > script_idx:
        m = None
        for m in _LIB_DIR_RE.finditer(path):
            pass
        if m is not None:
            lib = (path[m.end():].replace("\\", "/"), ln, func)
    return frames[script_idx][1], lib


# Reads the top "CLOSEST EXISTING NAMES" candidate back out of the report text rather than
# re-running the matching a third time, so the candidate detailed here can never disagree with
# the one shown. Two leading spaces, an identifier, then "(" is exactly `_report`'s own
# `f"  {c}{sig}"` formatting for the FIRST close-match line — keep the two in step.
_CLOSEST_NAME_RE = re.compile(r"CLOSEST EXISTING NAMES[^\n]*\n {2}(\w+)\(")


def _closest_class_replacement(module: str, report: str) -> str | None:
    """The dotted path `module.<name>` for the module report's top candidate, IF it is a class —
    else None. Imports locally; callers already refuse to run under `CMBAGENT_LG_REMOTE_EXECUTOR`.
    """
    m = _CLOSEST_NAME_RE.search(report)
    if not m:
        return None
    name = m.group(1)
    try:
        mod = importlib.import_module(module)
    except Exception:
        return None
    obj = getattr(mod, name, None)
    return f"{module}.{name}" if inspect.isclass(obj) else None


def module_shape_report(module: str, symbol: str) -> str | None:
    """The full injectable text (HEADER + report + STEER) for a caller that has ALREADY
    classified its own fault as `classify_api_error`'s "module" shape.

    Used by the bypass-after-fault guard (`self_debug/nodes.py`) so its nudge carries the real
    replacement, not just a warning. Silent under `CMBAGENT_LG_REMOTE_EXECUTOR`, and when the
    parent module itself does not import (that report repeats the traceback). When the top
    candidate is a CLASS, its method inventory is appended too — a constructor signature alone
    does not say how to call it; skipped silently when the candidate is not a class.
    """
    if os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR"):
        return None
    report = introspect_local(module, symbol)
    if report.startswith("introspection failed") or "could NOT be imported" in report.split(
            "\n", 1)[0]:
        return None
    dotted = _closest_class_replacement(module, report)
    if dotted is not None:
        class_report = introspect_full_class_local({"callable": dotted})
        if class_report:
            report = report + "\n\n" + class_report
    return "\n".join([_HEADER, "", report, "", _STEER])


_STEER_CALL = (
    "The failure above is a call that does not match the INSTALLED API (a parameter, an "
    "argument count, or an attribute's kind). Adapt the call to the signature and attributes "
    "shown above. Do not resubmit the same call, and do not work around it by inventing data."
)

# How many distinct stderr hits `retry_report` tries; each may start an introspection process.
_MAX_STDERR_HITS = 3
# Bound on the injected report body; a longer one is cut at a line, and says so.
_MAX_REPORT_CHARS = 4000

_QUOTED_NAME_RE = re.compile(r"'(\w+)'")


def _script_classes(code: str | None) -> tuple[set, set]:
    """`(names, attrs)` of the classes the SCRIPT defines: their names, and every attribute one
    of them carries — a class-body name (method, assignment) or a `self.<attr> = ...` in a
    method. A traceback that names one of these is about the engineer's own class, which a
    same-named library class must never answer for. `(set(), set())` when the script does not
    parse."""
    try:
        tree = ast.parse(code or "")
    except Exception:  # noqa: BLE001
        return set(), set()
    names, attrs = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names.add(node.name)
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                attrs.add(stmt.name)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for t in targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name):
                            attrs.add(sub.id)
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.args.args:
                me = sub.args.args[0].arg
                for st in ast.walk(sub):
                    targets = (st.targets if isinstance(st, ast.Assign) else
                               [st.target] if isinstance(st, (ast.AnnAssign, ast.AugAssign))
                               else [])
                    for t in targets:
                        for x in ast.walk(t):
                            if (isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
                                    and x.value.id == me):
                                attrs.add(x.attr)
    return names, attrs


def _script_imports(code: str, work_dir, apf):
    """`(modules, froms)` the script imports, minus anything that is engineer code."""
    modules, froms = _imported_modules(code)
    modules = [m for m in modules if not apf._workspace_root(m.split(".")[0], work_dir)]
    froms = [(m, n) for m, n in froms if not apf._workspace_root(m.split(".")[0], work_dir)]
    return modules, froms


def _retry_body(kind: str, m: "re.Match", code, stderr, work_dir, apf, why: list):
    """`(report, steer)` for one recognised error, or None. Appends the reason to `why` when
    silent."""
    if kind == "module":
        module, symbol = m.group("module"), m.group("symbol")
        if apf._workspace_root(module.split(".")[0], work_dir):
            why.append(f"module {module} is engineer code")
            return None
        report = env_report(SNIPPET, {"module": module, "symbol": symbol}, work_dir,
                            lambda: introspect_local(module, symbol))
        # "MODULE 'x' could NOT be imported" repeats what the traceback already said, so inject
        # nothing. Checked on the FIRST line only — a successful report also opens with
        # "MODULE ", and a docstring below it may say anything.
        if not report or report.startswith(
                "introspection failed") or "could NOT be imported" in report.split("\n", 1)[0]:
            why.append(f"module {module} not importable here")
            return None
        return report, _STEER
    if kind == "artist_kwarg":
        # No code needed: the class comes from matplotlib's own closed set of Artist subclasses,
        # not from what the failing script imported (see ARTIST_SNIPPET).
        spec = {"cls_name": m.group("cls"), "kwarg": m.group("kw")}
        report = env_report(ARTIST_SNIPPET, spec, work_dir,
                            lambda: introspect_artist_kwarg_local(spec))
        if not report:      # unresolvable/ambiguous class, or disagrees with the crash → silent
            why.append("artist class unresolved")
            return None
        # Distinct steer: the TOP-LEVEL call the engineer wrote is not itself wrong — only the
        # kwarg it forwarded, several frames deeper, to a matplotlib Artist.
        return report, (
            "The failure above is matplotlib rejecting an invalid property several frames "
            "inside a plotting call whose OWN signature accepts arbitrary keywords (so it could "
            "not be caught earlier) — it is not evidence the top-level function name is wrong. "
            "Drop the invalid keyword, or move it to wherever the report above says it actually "
            "belongs. Do not resubmit the same call, and do not work around it by inventing "
            "data.")
    # Every other shape is resolved from the failing script — no code, no guess.
    if not code:
        why.append("no script to resolve against")
        return None
    own_classes, own_attrs = _script_classes(code)
    if kind == "instance":
        if m.group("type") in own_classes:
            why.append(f"class {m.group('type')} is defined by the script")
            return None
        modules, froms = _script_imports(code, work_dir, apf)
        spec = {"type": m.group("type"), "attr": m.group("attr"), "modules": modules,
                "froms": froms}
        report = env_report(CLASS_SNIPPET, spec, work_dir, lambda: introspect_class_local(spec))
        if not report:
            why.append(f"class {spec['type']} unresolved or has {spec['attr']}")
            return None
        return report, _STEER
    if kind in ("signature_kw", "signature_arity", "signature_missing"):
        cls_name, func = m.groupdict().get("cls"), m.group("func")
        if cls_name and cls_name in own_classes:
            why.append(f"class {cls_name} is defined by the script")
            return None
        base = {"already_failed": True}
        if kind == "signature_kw":
            base["invalid_kwarg"] = m.group("kw")
        if kind == "signature_missing":
            base["missing"] = _QUOTED_NAME_RE.findall(m.group("names"))
        report = ""
        if cls_name:
            # CPython names the class that DEFINES the method (`CAMBparams.set_cosmology()`):
            # resolve it from the script's imports, whatever object the call was made on.
            modules, froms = _script_imports(code, work_dir, apf)
            spec = dict(base, type=cls_name, method=func, modules=modules, froms=froms)
            report = env_report(SIGNATURE_SNIPPET, spec, work_dir,
                                lambda: introspect_call_signature_local(spec))
            if report:
                return report, _STEER_CALL
        # The traceback's function, found through the script's own call sites. A qualified
        # message must agree with what that resolves to (`_qual_agrees`).
        target = cls_name if func == "__init__" and cls_name else func
        dotted = apf.resolve_dotted_call_by_func_name(code, target)
        if dotted is None:  # ambiguous or unresolvable — do not guess which callable it was
            why.append(f"{(cls_name + '.') if cls_name else ''}{func} unresolved from the script")
            return None
        spec = dict(base, callable=dotted)
        if cls_name:
            spec["qualname"] = f"{cls_name}.{func}"
        report = env_report(SIGNATURE_SNIPPET, spec, work_dir,
                            lambda: introspect_call_signature_local(spec))
        if not report:      # unprovable, or the resolution disagreed with the crash → silent
            why.append(f"no signature report for {dotted} (unresolvable here, disagrees with "
                       f"the error, or takes **kwargs)")
            return None
        return report, (_STEER_CALL if kind == "signature_missing" else _STEER)
    modules, froms = _script_imports(code, work_dir, apf)
    if kind == "property_setter":
        # Named by the message (type and attribute) and proven on the class the script's imports
        # resolve, so no script frame is needed: a printed or wrapped error has none to offer.
        if m.group("type") in own_classes:
            why.append(f"class {m.group('type')} is defined by the script")
            return None
        spec = {"shape": kind, "type": m.group("type"), "attr": m.group("attr"),
                "modules": modules, "froms": froms}
        report = env_report(SHAPE_SNIPPET, spec, work_dir, lambda: introspect_shape_local(spec))
        if not report:
            why.append(f"{kind}: nothing resolved uniquely")
            return None
        return report, _STEER_CALL
    # Shapes that name no function: the failing script line names it instead.
    line, lib = _traceback_frames(stderr, code)
    if line is None:
        why.append("no script frame in the traceback")
        return None
    calls = apf.calls_on_line(code, line)
    if kind in ("not_callable", "ctypes_arity"):
        # An attribute one of the script's own classes carries is not the library's to answer.
        attrs = sorted({c["attr"] for c in calls if c["attr"] and c["attr"] not in own_attrs})
        if not attrs:
            why.append("no library attribute call on the failing line")
            return None
        spec = {"shape": kind, "attrs": attrs, "modules": modules, "froms": froms}
        if kind == "not_callable":
            spec["type"] = m.group("type")
        else:
            spec.update(n=int(m.group("n")), m=int(m.group("m")))
    else:  # value_type / unknown_argument
        lib_calls = [c for c in calls if c["dotted"]
                     and not apf._workspace_root(c["dotted"].split(".")[0], work_dir)]
        # The call that led into the library: the one resolved library call on the line.
        dotted = {c["dotted"] for c in lib_calls}
        callee = next(iter(dotted)) if len(dotted) == 1 else None
        call = next((c for c in lib_calls if c["dotted"] == callee), None)
        kw = m.groupdict().get("kw") or ""
        if kind == "unknown_argument" and (call is None or (kw not in call["keywords"]
                                                            and not call["starstar"])):
            # The call on the line must be the one that passed the keyword (literally or
            # through `**`); otherwise the message is about something this line did not do.
            why.append(f"no library call on the failing line passes '{kw}'")
            return None
        roots = sorted({x.split(".")[0] for x in modules} | {f[0].split(".")[0] for f in froms})
        inner = list(lib) if lib is not None else None
        if callee is None and inner is None:
            why.append("no library call or library frame resolved")
            return None
        spec = {"shape": kind, "callee": callee, "inner": inner, "roots": roots,
                "kw": kw if kind == "unknown_argument" else "",
                "call_keywords": call["keywords"] if call else [],
                "modules": modules, "froms": froms}
        if kind == "value_type":
            spec["expected"] = m.groupdict().get("type") or "real number"
            spec["got"] = m.group("got")
    report = env_report(SHAPE_SNIPPET, spec, work_dir, lambda: introspect_shape_local(spec))
    if not report:
        why.append(f"{kind}: nothing resolved uniquely")
        return None
    return report, _STEER_CALL


def retry_report(ctx, stderr: str | None, code: str | None = None,
                 stdout: str | None = None, work_dir: str | None = None,
                 reasons: list | None = None) -> str | None:
    """Report to inject into the next engineer attempt, or None to leave the retry untouched.

    `code` is the script that produced this output; it is needed for every shape whose origin
    has to be resolved from the script's own imports, call sites or failing line. `stdout` is
    consulted only when stderr yields nothing, and accepts the prefix-less form (a script that
    caught and printed its own exception exits 0 with empty stderr). `work_dir` names the guest
    session under `CMBAGENT_LG_REMOTE_EXECUTOR` (see `env_report`). None — inject NOTHING —
    whenever anything is unprovable; never a hedged or partial answer. `reasons`, when given,
    receives one line saying why nothing was injected (for the engineer's log); it never changes
    the answer.
    """
    why: list = reasons if reasons is not None else []
    raw = os.environ.get("CMBAGENT_LG_API_INTROSPECT_RETRY")
    if raw == "0":
        why.append("disabled")
        return None
    if raw != "1" and not getattr(ctx, "api_introspect_retry", False):
        why.append("disabled")
        return None

    # Lazy import: `api_preflight` imports THIS module at load time, so a module-scope import
    # back would be a cycle. `_workspace_root` is the one rule for "importing this would run
    # engineer code": the traceback names a module, and the report would import it.
    from cmbagent_lg.self_debug import api_preflight as _apf

    # stderr first (a real traceback): the last hit, then the one `classify_api_error` picks
    # (they differ when a later kind follows an original one), then the other hits from last to
    # first, at most `_MAX_STDERR_HITS` in all. stdout (the caught-and-printed case) only when
    # stderr holds no original-kind hit, as before the later kinds existed.
    # Both streams are also read for library errors embedded in other text (`_all_matches`'s
    # `embedded`: an exception repr, a message inside another exception's message, a printed
    # error), and each hit is given the traceback it belongs to (`_hit_segment`).
    hits = []
    seen = set()
    err_all = _all_matches(stderr, embedded=True)
    picked = _pick(err_all)
    ordered = ([err_all[-1]] if err_all else []) + ([picked] if picked else []) + err_all[::-1]
    for start, kind, m in ordered:
        key = (kind, m.group(0))
        if key in seen:
            continue
        seen.add(key)
        hits.append(((kind, m), _hit_segment(stderr, start)))
        if len(hits) >= _MAX_STDERR_HITS:
            break
    if not any(h[0][0] in _ORIGINAL_KINDS for h in hits):
        out_hit = _pick(_all_matches(stdout, allow_bare=True, embedded=True))
        if out_hit is not None:
            hits.append(((out_hit[1], out_hit[2]), _hit_segment(stdout, out_hit[0])))
    if not hits:
        why.append("no API-shaped error in the output")
        return None
    for (kind, m), text in hits:
        try:
            body = _retry_body(kind, m, code, text, work_dir, _apf, why)
        except Exception as e:  # noqa: BLE001 — a retry aid must never break the step
            why.append(f"{kind}: {type(e).__name__}: {e}")
            body = None
        if body is not None:
            report, steer = body
            return "\n".join([_HEADER, "", _cap_report(report), "", steer])
    return None


def _cap_report(report: str, limit: int = _MAX_REPORT_CHARS) -> str:
    """`report` cut at the last whole line within `limit` characters, with a line saying how
    many lines were left out; unchanged when it fits."""
    if len(report) <= limit:
        return report
    lines = report.split("\n")
    kept, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit:
            break
        kept.append(line)
        size += len(line) + 1
    if not kept:  # one very long line: cut it
        kept = [lines[0][:limit]]
    return "\n".join(kept + [f"[report cut here: {len(lines) - len(kept)} more lines omitted]"])


__all__ = ["SNIPPET", "CLASS_SNIPPET", "SIGNATURE_SNIPPET", "ARTIST_SNIPPET", "SHAPE_SNIPPET",
           "introspect_local", "introspect_shape_local",
           "introspect_class_local", "introspect_callable_class_local",
           "introspect_full_class_local", "introspect_call_signature_local",
           "introspect_artist_kwarg_local", "parse_api_error", "classify_api_error",
           "kwarg_error_qualifier",
           "retry_report", "module_shape_report", "env_report"]
