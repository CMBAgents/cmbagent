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
import difflib, importlib, inspect, json, re, sys

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
_PATTERNS = (
    tuple(("module", p) for p in _ERROR_PATTERNS)
    + (("instance", _INSTANCE_PATTERN),)
    + (("signature_kw", _SIGNATURE_KWARG_PATTERN), ("signature_arity", _SIGNATURE_ARITY_PATTERN))
    + (("artist_kwarg", _ARTIST_SET_PATTERN),)
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
    """The class qualifier of the LAST call-shape TypeError in `stderr` (`Mixin` in
    `Mixin.score() got an unexpected keyword argument`), or None when the message
    names a bare function. Companion to `classify_api_error`, whose tuple shape is fixed."""
    best = None
    for pat in (_SIGNATURE_KWARG_PATTERN, _SIGNATURE_ARITY_PATTERN):
        for m in pat.finditer(stderr or ""):
            if best is None or m.start() > best.start():
                best = m
    return best.group("cls") if best is not None else None


def classify_api_error(stderr: str | None, allow_bare: bool = False) -> tuple[str, str, str] | None:
    """`("module", module, symbol)` / `("instance", type_name, attribute)` /
    `("signature", func_name, invalid_kwarg_or_empty)` / `("artist_kwarg", class_name, kwarg)` /
    None.

    The `"signature"` third element is `""` for the arity form, which names no keyword. Returns
    the LAST recognised error in the text — a traceback ends with the exception that actually
    killed the run. `allow_bare` also accepts the message with no exception-name prefix (a
    caught-and-printed `str(e)`); reserved for STDOUT, since stderr carries a real traceback.
    """
    best: tuple[int, str, "re.Match"] | None = None
    for kind, pat in _PATTERNS + (_BARE_PATTERNS if allow_bare else ()):
        for m in pat.finditer(stderr or ""):
            if best is None or m.start() > best[0]:
                best = (m.start(), kind, m)
    if best is None:
        return None
    _, kind, m = best
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

    `anndata.AnnData` is not a module, so import_module fails on the whole path; the split
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
    the return annotation. Measured: anndata.read_h5ad's annotation is the STRING "AnnData"
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
    anndata.AnnData defines neither hook and is judgeable.
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

def _signature_report(spec):
    """Report for a call PROVEN to conflict with the RESOLVED signature, else "" — including
    when the signature accepts `**kwargs`, which means nothing can be proven at this boundary (a
    permissive signature says any keyword MAY be valid)."""
    path = spec.get("callable", "")
    if not path:
        return ""
    obj = _resolve_dotted(path)
    if obj is None or not callable(obj):
        return ""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return ""

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


def retry_report(ctx, stderr: str | None, code: str | None = None,
                 stdout: str | None = None, work_dir: str | None = None) -> str | None:
    """Report to inject into the next engineer attempt, or None to leave the retry untouched.

    `code` is the script that produced this output; it is needed only for the instance and call
    shapes, whose origin has to be resolved from the script's own imports/call sites. `stdout`
    is consulted only when stderr yields nothing, and accepts the prefix-less form (a script
    that caught and printed its own exception exits 0 with empty stderr). `work_dir` names the
    guest session under `CMBAGENT_LG_REMOTE_EXECUTOR` (see `env_report`). None — inject NOTHING
    — whenever anything is unprovable; never a hedged or partial answer.
    """
    raw = os.environ.get("CMBAGENT_LG_API_INTROSPECT_RETRY")
    if raw == "0":
        return None
    if raw != "1" and not getattr(ctx, "api_introspect_retry", False):
        return None

    # stderr first (a real traceback), then stdout for the caught-and-printed case.
    hit = classify_api_error(stderr) or classify_api_error(stdout, allow_bare=True)
    if hit is None:
        return None

    # Lazy import: `api_preflight` imports THIS module at load time, so a module-scope import
    # back would be a cycle. `_workspace_root` is the one rule for "importing this would run
    # engineer code": the traceback names a module, and the report would import it.
    from cmbagent_lg.self_debug import api_preflight as _apf

    if hit[0] == "module":
        if _apf._workspace_root(hit[1].split(".")[0], work_dir):
            return None
        report = env_report(SNIPPET, {"module": hit[1], "symbol": hit[2]}, work_dir,
                            lambda: introspect_local(hit[1], hit[2]))
        # "MODULE 'x' could NOT be imported" repeats what the traceback already said, so inject
        # nothing. Checked on the FIRST line only — a successful report also opens with
        # "MODULE ", and a docstring below it may say anything.
        if not report or report.startswith(
                "introspection failed") or "could NOT be imported" in report.split("\n", 1)[0]:
            return None
    elif hit[0] == "instance":
        # Instance shape: resolvable only from the script's own imports, so without the code
        # we leave it alone rather than guess which module the type came from.
        if not code:
            return None
        modules, froms = _imported_modules(code)
        # Never hand the resolver a module that is engineer code (`step_N`, anything in the
        # workspace): `_resolve` imports every module it is given.
        modules = [m for m in modules if not _apf._workspace_root(m.split(".")[0], work_dir)]
        froms = [(m, n) for m, n in froms if not _apf._workspace_root(m.split(".")[0], work_dir)]
        spec = {"type": hit[1], "attr": hit[2], "modules": modules, "froms": froms}
        report = env_report(CLASS_SNIPPET, spec, work_dir,
                            lambda: introspect_class_local(spec))
        if not report:      # unprovable → stay silent
            return None
    elif hit[0] == "artist_kwarg":
        # No code needed: the class comes from matplotlib's own closed set of Artist subclasses,
        # not from what the failing script imported (see ARTIST_SNIPPET).
        spec = {"cls_name": hit[1], "kwarg": hit[2]}
        report = env_report(ARTIST_SNIPPET, spec, work_dir,
                            lambda: introspect_artist_kwarg_local(spec))
        if not report:      # unresolvable/ambiguous class, or disagrees with the crash → silent
            return None
        # Distinct steer: the TOP-LEVEL call the engineer wrote is not itself wrong — only the
        # kwarg it forwarded, several frames deeper, to a matplotlib Artist.
        return "\n".join([_HEADER, "", report, "", (
            "The failure above is matplotlib rejecting an invalid property several frames "
            "inside a plotting call whose OWN signature accepts arbitrary keywords (so it could "
            "not be caught earlier) — it is not evidence the top-level function name is wrong. "
            "Drop the invalid keyword, or move it to wherever the report above says it actually "
            "belongs. Do not resubmit the same call, and do not work around it by inventing "
            "data.")])
    else:
        # Call shape: the traceback names only the bare function, so the dotted path has to come
        # from the failing SCRIPT's own call sites — same "no code, no guess" rule as above.
        if not code:
            return None
        dotted = _apf.resolve_dotted_call_by_func_name(code, hit[1])
        if dotted is None:  # ambiguous or unresolvable — do not guess which callable it was
            return None
        spec = {"callable": dotted, "already_failed": True}
        if hit[2]:
            spec["invalid_kwarg"] = hit[2]
        report = env_report(SIGNATURE_SNIPPET, spec, work_dir,
                            lambda: introspect_call_signature_local(spec))
        if not report:      # unprovable, or the resolution disagreed with the crash → silent
            return None
    return "\n".join([_HEADER, "", report, "", _STEER])


__all__ = ["SNIPPET", "CLASS_SNIPPET", "SIGNATURE_SNIPPET", "ARTIST_SNIPPET", "introspect_local",
           "introspect_class_local", "introspect_callable_class_local",
           "introspect_full_class_local", "introspect_call_signature_local",
           "introspect_artist_kwarg_local", "parse_api_error", "classify_api_error",
           "kwarg_error_qualifier",
           "retry_report", "module_shape_report", "env_report"]
