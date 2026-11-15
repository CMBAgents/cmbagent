"""Scope-aware receiver typing for the API pre-flight — `obj.method(...)` where `obj` is never
named as a class in the script.

The module walk in `api_preflight.py` judges `mod.symbol`; this module judges `name.attr` when
the CLASS of `name` is provable from the script alone plus the installed package's own
introspectable facts. Nothing here executes engineer code: every verdict comes from `ast` over
the script and `inspect` over objects the installed packages already expose.

How a name gets a class ("typed"), per scope:

  * `name = <call>` where the callee resolves to a CLASS (`model = Estimator()`), or to a
    function whose produced class the environment states (`_produced_class`: a return
    annotation, a Boost/`:rtype:` docstring header, a numpydoc `Returns self`), or to a method
    of an already typed name (`child = parent.make_child(...)`);
  * `fig, ax = plt.subplots(...)`, iteration over such an axes array, and iteration over a
    call a curated entry declares the element class of (`iter_produces`) — through the small
    witness-gated table `_CURATED` (matplotlib and numpy constructors only: universal
    libraries the engineer template itself mandates, never anything task- or
    dataset-specific) plus whatever a caller's EXTENSION file declares in the same shape
    (`load_extensions`; a fleet ships its own domain entries there, nothing here knows them);
  * and NOTHING else. A name that is a parameter, `global`/`nonlocal` anywhere (which also
    untypes the name in the scope it rebinds), bound inside a `try:`, bound by more than one
    shape (or two shapes that disagree), touched by `getattr`/`setattr`/`hasattr`/`delattr`/
    `vars` anywhere, aliased, or whose attributes are ever ASSIGNED in the script is untyped,
    and every question about it is silent. `exec`/`eval`/`globals()`/`locals()` anywhere make
    the whole typer inert.

Scopes follow Python's: the module, each `def`/`lambda`, each comprehension, each class body
(which method bodies skip on lookup, as the language does). A name bound in a function is that
function's; a name only READ there resolves in the enclosing scopes.

The verdicts (`absent_on_typed_receivers`, `kwarg_mismatch_on_typed_receivers`) hold to the
same "prove or stay silent" bar as the rest of the pre-flight; the guards are listed on each.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import sys
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple


# A class with this many loaded subclasses is a plugin point (registered as they are used):
# the class a FACTORY declares then says little about the object it hands back. One constant,
# shared with `api_introspect`'s environment-side snippet, so the two tiers agree.
PLUGIN_HIERARCHY_MIN_SUBCLASSES = 3


class TypeBinding(NamedTuple):
    cls: type
    kind: str          # "instance" | "array_1d" | "array_2d" (an array of `cls` instances)
    # True when the object IS an instance of `cls` (a constructor call, an exact-class
    # constructor from the curated table); False when `cls` is only the class a factory
    # DECLARES (annotation, docstring), which the real object may subclass.
    exact: bool = True


class AbsentFinding(NamedTuple):
    cls: type
    attr: str
    name: str          # the receiver variable, for the log line
    called: bool


class KwargFinding(NamedTuple):
    label: str         # `Class.method` or the dotted module path, for the report
    obj: object        # the callable whose signature was judged
    sig: inspect.Signature
    unknown: List[str]
    forwarded_to: Optional[str]   # dotted path of the target when a forwarder was judged


# ── the produced-class ladder ────────────────────────────────────────────────

# Boost.Python writes the C++ signature as the first docstring line:
#   `make_thing( (object)arg, (Params)params) -> Thing :`
_BOOST_RE = re.compile(r"^\w+\(.*\) -> (\w+) :", re.M)
# Sphinx field lists, as pybind/nanobind wrappers and hand-written docstrings use them.
_RTYPE_RE = re.compile(r":rtype:\s*(\w+)\s*$", re.M)
# numpydoc: the method returns the receiver itself (`fit` on every sklearn estimator).
_RETURNS_SELF_RE = re.compile(r"Returns\s*\n\s*-{3,}\s*\n\s*self\b")

_class_cache: Dict[Tuple[str, str], Optional[type]] = {}
_produced_cache: Dict[Tuple[int, int], Optional[type]] = {}


def _root_of(obj) -> str:
    mod = getattr(obj, "__module__", None)
    return (mod or "").split(".")[0] if isinstance(mod, str) else ""


def _class_named(name: str, obj) -> Optional[type]:
    """The ONE class called `name` in the callable's own package, or None.

    Looked up on the callable's own module first (a string annotation is written against it),
    then across every LOADED module of the same root package. Two DIFFERENT classes sharing the
    name is ambiguity, and ambiguity is refused: picking one would be a guess. Builtins are never
    reached (`-> int :` names nothing under the package), which is deliberate — `str.get_text`
    is not a question this module can ask honestly.
    """
    own = sys.modules.get(getattr(obj, "__module__", "") or "")
    hit = getattr(own, name, None)
    if inspect.isclass(hit) and hit.__name__ == name:
        return hit
    root = _root_of(obj)
    if not root or not name:
        return None
    key = (root, name)
    if key in _class_cache:
        return _class_cache[key]
    found: Dict[int, type] = {}
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not (mod_name == root or mod_name.startswith(root + ".")):
            continue
        cand = getattr(mod, name, None)
        if inspect.isclass(cand) and cand.__name__ == name and _root_of(cand) == root:
            found.setdefault(id(cand), cand)
    cls = list(found.values())[0] if len(found) == 1 else None
    _class_cache[key] = cls
    return cls


def _produced_class(obj, receiver_cls: Optional[type] = None) -> Optional[type]:
    """The class a call to `obj` produces, when the ENVIRONMENT states it — else None.

    Strict ladder, first rung that answers wins:
      1. a class produces itself;
      2. the return annotation — a class, or a bare name resolving to one class of the same
         package. An annotation that says anything else (a union, `Any`, a subscript) is the
         library saying the answer is not one class, so the ladder STOPS there;
      3. a docstring header in a machine form only (Boost's `f(...) -> Cls :`, Sphinx `:rtype:`),
         every occurrence agreeing;
      4. numpydoc `Returns / ---- / self` ⇒ the receiver's own class;
      5. the curated table (`_curated_entry`), witness-gated.
    Prose is never read.
    """
    if inspect.isclass(obj):
        return obj
    if not callable(obj):
        return None
    key = (id(obj), id(receiver_cls))
    if key in _produced_cache:
        return _produced_cache[key]
    cls = _produced_class_uncached(obj, receiver_cls)
    _produced_cache[key] = cls
    return cls


def _produced_class_uncached(obj, receiver_cls):
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        sig = None
    if sig is not None and sig.return_annotation is not inspect.Signature.empty:
        ann = sig.return_annotation
        if inspect.isclass(ann):
            return ann
        if isinstance(ann, str):
            name = ann.strip().strip("'\"")
            if name.isidentifier():
                return _class_named(name, obj)
        return None
    try:
        doc = inspect.getdoc(obj) or ""
    except Exception:  # noqa: BLE001
        doc = ""
    names = set(_BOOST_RE.findall(doc[:4000])) or set(_RTYPE_RE.findall(doc[:4000]))
    if len(names) == 1:
        return _class_named(names.pop(), obj)
    if names:
        return None  # overloads that disagree on what they return
    if receiver_cls is not None and _RETURNS_SELF_RE.search(doc):
        return receiver_cls
    entry = _curated_entry(obj)
    if entry is not None and entry.get("produces") is not None:
        return entry["produces"]()
    return None


# ── the curated table ────────────────────────────────────────────────────────
#
# Facts `inspect` cannot state but the library's own source does. Deliberately tiny and limited
# to the two libraries the engineer template itself prescribes (`import matplotlib.pyplot as
# plt`, numpy); nothing here knows about any task or dataset. Every entry carries a WITNESS,
# evaluated once per process against the installed package: if the witness fails (a release
# changed the shape the entry relies on) the entry is silently disabled rather than trusted.
#
# Entries are keyed by the identity of the resolved callable, so `plt.subplots`, `pyplot.subplots`
# and `matplotlib.pyplot.subplots` all hit the same row, and a script's own `def subplots` never
# does.

_CURATED_SPECS: List[dict] = []
_curated_by_id: Dict[int, dict] = {}
_curated_pending: List[dict] = []
_curated_initialised = False


def _import_quiet(name: str):
    """Only ever called for a root the SCRIPT already imports (the entry's root must be in
    `sys.modules` before the witness runs), so this never adds an import the check did not
    already pay for."""
    try:
        return sys.modules.get(name) or __import__(name, fromlist=["_"])
    except BaseException:  # noqa: BLE001
        return None


def _resolve_path(dotted: str):
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        mod = sys.modules.get(".".join(parts[:i]))
        if mod is None:
            continue
        obj = mod
        for p in parts[i:]:
            try:
                obj = inspect.getattr_static(obj, p) if inspect.isclass(obj) else getattr(obj, p)
            except AttributeError:
                return None
        return obj
    return None


def _mpl_subplots_witness() -> bool:
    plt = _import_quiet("matplotlib.pyplot")
    fig_mod = _import_quiet("matplotlib.figure")
    axes_mod = _import_quiet("matplotlib.axes")
    if plt is None or fig_mod is None or axes_mod is None:
        return False
    if not (inspect.isclass(getattr(fig_mod, "Figure", None))
            and inspect.isclass(getattr(axes_mod, "Axes", None))):
        return False
    try:
        ann = inspect.signature(plt.subplots).return_annotation
    except (TypeError, ValueError):
        return False
    return isinstance(ann, str) and ann.replace(" ", "").startswith("tuple[Figure,")


def _mpl_legend_witness(owner_path: str) -> bool:
    """The forwarder shape the entry relies on, read from the installed source: `Axes.legend` and
    `Figure.legend` hand every remaining keyword to `Legend(self, handles, labels, **kwargs)`,
    and `pyplot.legend` is `gca().legend(*args, **kwargs)`."""
    legend_mod = _import_quiet("matplotlib.legend")
    if legend_mod is None or not inspect.isclass(getattr(legend_mod, "Legend", None)):
        return False
    fn = _resolve_path(owner_path)
    if fn is None:
        return False
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    if owner_path == "matplotlib.pyplot.legend":
        return "gca().legend(*args, **kwargs)" in src and _mpl_legend_witness(
            "matplotlib.axes.Axes.legend")
    return re.search(r"Legend\(self, handles, labels, \*\*kwargs\)", src) is not None


def _numpy_witness(name: str) -> bool:
    np = _import_quiet("numpy")
    if np is None or not inspect.isclass(getattr(np, "ndarray", None)):
        return False
    fn = getattr(np, name, None)
    return callable(fn) and (getattr(fn, "__module__", "") or "").split(".")[0] == "numpy"


def _numpy_ndarray():
    return getattr(sys.modules.get("numpy"), "ndarray", None)


def _mpl_class(mod: str, name: str):
    return getattr(sys.modules.get(mod), name, None)


def _class_at(dotted: str):
    """A resolver for the class at `dotted` (loaded modules only), for declarative entries."""
    def resolve():
        obj = _resolve_path(dotted)
        return obj if inspect.isclass(obj) else None
    return resolve


for _legend_path in ("matplotlib.pyplot.legend", "matplotlib.axes.Axes.legend",
                     "matplotlib.figure.Figure.legend"):
    _CURATED_SPECS.append({
        "path": _legend_path, "root": "matplotlib",
        "module": _legend_path.rsplit(".", 2)[0] if _legend_path.count(".") > 2 else "matplotlib.pyplot",
        "witness": (lambda p=_legend_path: _mpl_legend_witness(p)),
        # Keywords the FORWARDER itself accepts before handing the rest to the target; the
        # target's own `self`/`parent` are never spellable by a caller.
        "forward_to": "matplotlib.legend.Legend.__init__",
        "forward_accepts": {"handles", "labels"},
        "forward_hides": {"self", "parent"},
    })
_CURATED_SPECS.append({
    "path": "matplotlib.pyplot.subplots", "root": "matplotlib", "module": "matplotlib.pyplot",
    "witness": _mpl_subplots_witness, "subplots": True,
    # What the `(first, rest)` tuple unpacks to: the first element's class, and the class of
    # every element of the array the rest is (or of the single object when the grid is 1x1).
    "produces": (lambda: _mpl_class("matplotlib.figure", "Figure")),
    "elem": (lambda: _mpl_class("matplotlib.axes", "Axes")),
})
for _np_name in ("array", "asarray", "zeros", "ones", "empty", "linspace", "arange", "full"):
    _CURATED_SPECS.append({
        "path": "numpy." + _np_name, "root": "numpy", "module": "numpy",
        "witness": (lambda n=_np_name: _numpy_witness(n)),
        "produces": _numpy_ndarray,
        # Keywords under which the call no longer returns a plain array.
        "refuse_kwargs": {"like", "subok", "retstep"},
    })


# ── extensions: the same table shape, supplied from OUTSIDE the package ─────
#
# A caller (a fleet, a domain deployment) may know facts about its own libraries that
# `inspect` cannot state — which class a factory produces, what iterating a call yields, which
# forwarder hands its keywords on. Those facts never live here: they are DECLARED in a JSON
# file the caller points at (`PlanContext.api_preflight_extensions`, env twin
# `CMBAGENT_LG_API_EXTENSIONS`), each entry witness-gated exactly like the built-in rows, and an
# entry whose witness fails is silently disabled. With no file, nothing changes.
#
# File shape — a JSON list of objects:
#   {"path": "pkg.mod.factory",            # the callable, by dotted path
#    "root": "pkg", "module": "pkg.mod",   # the package that must already be loaded; the entry's
#                                          #   own submodule (imported by the loader, same package)
#    "produces": "pkg.mod.Cls"             # OR
#    "iter_produces": "pkg.mod.Elem"       # OR
#    "forward_to": "pkg.mod.Target.__init__", "forward_accepts": [..], "forward_hides": [..],
#    "refuse_kwargs": [..],                # optional: keywords under which the entry is void
#    "exact": false,                       # optional: True = the object IS that class (never a
#                                          #   subclass); default False = a declared class
#    "witness": {"class": "pkg.mod.Cls", "method": "name"}}   # required: both must resolve
_extensions_loaded: Dict[str, Tuple[float, int]] = {}


def _declarative_witness(spec: dict):
    """The witness for a declarative entry: its class exists, its method resolves on that class
    (statically, so no descriptor runs), and whatever it declares to produce is a class."""
    w = spec.get("witness") or {}

    def witness() -> bool:
        cls = _resolve_path(str(w.get("class", "")))
        if not inspect.isclass(cls):
            return False
        try:
            if inspect.getattr_static(cls, str(w.get("method", ""))) is None:
                return False
        except AttributeError:
            return False
        for key in ("produces", "iter_produces"):
            if key in spec and not inspect.isclass(spec[key]()):
                return False
        if spec.get("forward_to") and _resolve_path(spec["forward_to"]) is None:
            return False
        return True

    return witness


def load_extensions(path: Optional[str]) -> int:
    """Load a caller's extension file into the pending table, once per (path, mtime, size).
    Returns the number of entries queued. Never raises: a missing, unreadable or malformed
    file loads nothing, and a malformed ENTRY is skipped."""
    if not path:
        return 0
    try:
        import json
        st = os.stat(path)
        stamp = (st.st_mtime, st.st_size)
        if _extensions_loaded.get(path) == stamp:
            return 0
        _extensions_loaded[path] = stamp
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:  # noqa: BLE001 — an unreadable file is an empty one
        return 0
    if not isinstance(raw, list):
        return 0
    # Drop anything a previous version of this file queued or resolved.
    _curated_pending[:] = [s for s in _curated_pending if s.get("_source") != path]
    for k in [k for k, s in _curated_by_id.items() if s.get("_source") == path]:
        del _curated_by_id[k]
    n = 0
    for item in raw:
        spec = _declarative_spec(item, path)
        if spec is not None:
            _curated_pending.append(spec)
            n += 1
    _produced_cache.clear()  # a "produces nothing" answer may now have an entry
    return n


def _declarative_spec(item, source: str) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    try:
        path_, root, module = str(item["path"]), str(item["root"]), str(item["module"])
        witness = item["witness"]
        if not (path_ and root and module and isinstance(witness, dict)
                and witness.get("class") and witness.get("method")):
            return None
        spec: dict = {"path": path_, "root": root, "module": module, "witness": witness,
                      "_source": source, "exact": bool(item.get("exact", False))}
        if isinstance(item.get("produces"), str):
            spec["produces"] = _class_at(item["produces"])
        if isinstance(item.get("iter_produces"), str):
            spec["iter_produces"] = _class_at(item["iter_produces"])
        if isinstance(item.get("forward_to"), str):
            spec["forward_to"] = item["forward_to"]
            spec["forward_accepts"] = set(map(str, item.get("forward_accepts") or []))
            spec["forward_hides"] = set(map(str, item.get("forward_hides") or []))
        if item.get("refuse_kwargs"):
            spec["refuse_kwargs"] = set(map(str, item["refuse_kwargs"]))
        if not any(k in spec for k in ("produces", "iter_produces", "forward_to")):
            return None
        spec["witness"] = _declarative_witness(spec)
        return spec
    except (KeyError, TypeError, ValueError):
        return None


def _curated_entry(obj) -> Optional[dict]:
    """The table row for `obj`, or None. Rows are resolved lazily, only once their root package
    is loaded (the script imported it), and only after their witness passes."""
    global _curated_initialised
    if not _curated_initialised:
        _curated_pending.extend(_CURATED_SPECS)
        _curated_initialised = True
    if _curated_pending:
        still = []
        for spec in _curated_pending:
            root_mod = sys.modules.get(spec["root"])
            # Not loaded yet, or already judged against THIS module object (a test may put a
            # stand-in under the same name and take it out again): wait for a change.
            if root_mod is None or spec.get("_failed_on") == id(root_mod):
                still.append(spec)
                continue
            # The entry's own submodule first (`matplotlib.pyplot` when the script so far only
            # imported `matplotlib` — same package, so no import the check did not already
            # pay for), then the witness, then the path resolves.
            try:
                _import_quiet(spec["module"])
                ok = spec["witness"]()
                target = _resolve_path(spec["path"]) if ok else None
            except Exception:  # noqa: BLE001 — a failed witness disables the entry, silently
                target = None
            if target is not None:
                _curated_by_id[id(target)] = spec
                # An answer memoised before this row resolved may have been "nothing".
                _produced_cache.clear()
            else:
                spec["_failed_on"] = id(root_mod)
                still.append(spec)
        _curated_pending[:] = still
    return _curated_by_id.get(id(obj))


def _subplots_shape(call: ast.Call) -> Optional[Tuple[int, int]]:
    """`(nrows, ncols)` for a `plt.subplots(...)` call whose shape is a literal, else None.

    Refused outright with `squeeze=`, `subplot_kw=`, `projection=`, any `**`/`*` unpacking or a
    non-literal dimension: each of those changes what comes back.
    """
    if any(kw.arg is None for kw in call.keywords) or any(
            isinstance(a, ast.Starred) for a in call.args):
        return None
    kws = {kw.arg: kw.value for kw in call.keywords}
    if {"squeeze", "subplot_kw", "projection"} & set(kws):
        return None
    dims = [1, 1]
    for i, a in enumerate(call.args[:2]):
        if not (isinstance(a, ast.Constant) and type(a.value) is int and a.value >= 1):
            return None
        dims[i] = a.value
    if len(call.args) > 2:
        return None
    for i, key in enumerate(("nrows", "ncols")):
        if key in kws:
            v = kws[key]
            if not (isinstance(v, ast.Constant) and type(v.value) is int and v.value >= 1):
                return None
            dims[i] = v.value
    return dims[0], dims[1]


# ── scopes ───────────────────────────────────────────────────────────────────


class _Scope:
    __slots__ = ("node", "parent", "is_class", "params", "bindings", "blocked")

    def __init__(self, node, parent, is_class=False):
        self.node = node
        self.parent = parent
        self.is_class = is_class
        self.params: set = set()
        self.bindings: Dict[str, list] = {}
        self.blocked: set = set()        # global / nonlocal declarations

    def bind(self, name: str, binding) -> None:
        self.bindings.setdefault(name, []).append(binding)


_OTHER = ("other", None)


def _has_starred(target) -> bool:
    return any(isinstance(n, ast.Starred) for n in ast.walk(target))


class _Builder:
    """One pass over the tree: builds the scope tree, records every name binding by shape, and
    remembers which scope each Attribute/Call node lives in."""

    def __init__(self, guarded: set):
        self.guarded = guarded
        self.owner: Dict[int, _Scope] = {}
        self.scopes: List[_Scope] = []
        self.class_names: set = set()
        self.cur: Optional[_Scope] = None

    def build(self, tree: ast.AST) -> "_Builder":
        self.cur = _Scope(tree, None)
        self.scopes.append(self.cur)
        for stmt in tree.body:
            self.visit(stmt)
        return self

    def _push(self, node, is_class=False) -> _Scope:
        sc = _Scope(node, self.cur, is_class)
        self.scopes.append(sc)
        self.cur = sc
        return sc

    def _pop(self) -> None:
        self.cur = self.cur.parent

    def _bind_target(self, target, value, guarded: bool) -> None:
        """Record what an assignment binds, by shape."""
        if isinstance(target, ast.Name):
            self.cur.bind(target.id, _OTHER if guarded or value is None else ("value", value))
        elif isinstance(target, (ast.Tuple, ast.List)):
            if guarded or value is None or _has_starred(target):
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        self.cur.bind(n.id, _OTHER)
                return
            self._bind_unpack(target, value, [], [])
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, None, guarded)
        # Attribute / Subscript targets bind no NAME (they are `_assigned_attributes`' business).

    def _bind_unpack(self, target, value, path, shape) -> None:
        shape = shape + [len(target.elts)]
        for i, elt in enumerate(target.elts):
            if isinstance(elt, ast.Name):
                self.cur.bind(elt.id, ("unpack", value, path + [i], shape))
            elif isinstance(elt, (ast.Tuple, ast.List)):
                self._bind_unpack(elt, value, path + [i], shape)
            else:
                for n in ast.walk(elt):
                    if isinstance(n, ast.Name):
                        self.cur.bind(n.id, _OTHER)

    def _bind_iter(self, target, iter_node, guarded: bool) -> None:
        if isinstance(target, ast.Name):
            self.cur.bind(target.id, _OTHER if guarded else ("iter", iter_node))
            return
        # `for i, ax in enumerate(axes)`: the second name iterates the argument.
        if (not guarded and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2
                and all(isinstance(e, ast.Name) for e in target.elts)
                and isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name)
                and iter_node.func.id == "enumerate" and len(iter_node.args) == 1
                and not iter_node.keywords):
            self.cur.bind(target.elts[0].id, _OTHER)
            self.cur.bind(target.elts[1].id, ("iter", iter_node.args[0]))
            return
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                self.cur.bind(n.id, _OTHER)

    def visit(self, node) -> None:  # noqa: C901 — one dispatch table, kept flat on purpose
        guarded = id(node) in self.guarded
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.cur.bind(node.name, _OTHER)
            for d in node.decorator_list:
                self.visit(d)
            for d in node.args.defaults + node.args.kw_defaults:
                if d is not None:
                    self.visit(d)
            self._push(node)
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    self.cur.params.add(arg.arg)
            for stmt in node.body:
                self.visit(stmt)
            self._pop()
            return
        if isinstance(node, ast.Lambda):
            self._push(node)
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    self.cur.params.add(arg.arg)
            self.visit(node.body)
            self._pop()
            return
        if isinstance(node, ast.ClassDef):
            self.cur.bind(node.name, _OTHER)
            self.class_names.add(node.name)
            for d in node.decorator_list + node.bases:
                self.visit(d)
            self._push(node, is_class=True)
            for stmt in node.body:
                self.visit(stmt)
            self._pop()
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            self._push(node)
            for gen in node.generators:
                self.visit(gen.iter)
                self._bind_iter(gen.target, gen.iter, guarded)
                for cond in gen.ifs:
                    self.visit(cond)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            self._pop()
            return
        if isinstance(node, ast.Assign):
            self.visit(node.value)
            for t in node.targets:
                self._bind_target(t, node.value, guarded)
                self._visit_target_reads(t)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.visit(node.value)
            self._bind_target(node.target, node.value, guarded)
            self._visit_target_reads(node.target)
            return
        if isinstance(node, (ast.AugAssign, ast.NamedExpr)):
            self.visit(node.value)
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    self.cur.bind(n.id, _OTHER)
            self._visit_target_reads(node.target)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.visit(node.iter)
            self._bind_iter(node.target, node.iter, guarded)
            self._visit_target_reads(node.target)
            for stmt in node.body + node.orelse:
                self.visit(stmt)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            self.cur.bind(n.id, _OTHER)
            for stmt in node.body:
                self.visit(stmt)
            return
        if isinstance(node, ast.ExceptHandler):
            if node.name:
                self.cur.bind(node.name, _OTHER)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                self.cur.bind(a.asname or a.name.split(".")[0], _OTHER)
            return
        elif isinstance(node, ast.Global):
            # The declaring scope never types the name; and the MODULE now has a binding it
            # cannot see the shape of (the assignment sits in this function), so the module-
            # level name disagrees with itself from here on.
            self.cur.blocked.update(node.names)
            for name in node.names:
                self.scopes[0].bind(name, _OTHER)
            return
        elif isinstance(node, ast.Nonlocal):
            # Same for every enclosing function scope: whichever one owns the name is the one
            # this function rebinds, and binding `_OTHER` in a scope that never owned it only
            # makes a lookup there silent.
            self.cur.blocked.update(node.names)
            sc = self.cur.parent
            while sc is not None and sc.parent is not None:
                if not sc.is_class:
                    for name in node.names:
                        sc.bind(name, _OTHER)
                sc = sc.parent
            return
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        self.cur.bind(n.id, _OTHER)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name:
                self.cur.bind(node.name, _OTHER)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            self.cur.bind(node.rest, _OTHER)
        elif isinstance(node, (ast.Attribute, ast.Call)):
            self.owner[id(node)] = self.cur
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def _visit_target_reads(self, target) -> None:
        """Attribute/Subscript targets READ their object (`ax.title = ...` reads `ax`)."""
        for n in ast.walk(target):
            if isinstance(n, (ast.Attribute, ast.Call)) and id(n) not in self.owner:
                self.owner[id(n)] = self.cur


# ── typing ───────────────────────────────────────────────────────────────────


class Typer:
    """Answers "what class is `name` in this scope?" — None whenever it is not provable.

    `trusted` / `from_aliases` are the pre-flight's own alias tables (an unambiguous, never
    rebound import); `resolve_dotted` turns a trusted dotted path into the installed object, or
    None, and is where the caller's import budget lives.
    """

    def __init__(self, tree: ast.AST, guarded: set, trusted: dict, from_aliases: set,
                 dynamic: set, assigned_roots: set,
                 resolve_dotted: Callable[[str], object],
                 assigned_attrs: Optional[set] = None, inert: bool = False):
        self.tree = tree
        self.guarded = guarded
        self.trusted = trusted
        self.from_aliases = from_aliases
        self.dynamic = dynamic
        self.assigned_roots = assigned_roots
        # Attribute NAMES the script assigns anywhere (`Cls.explain = fn`): no class can be
        # asked whether an instance has one of these.
        self.assigned_attrs = set(assigned_attrs or ())
        # `exec`/`eval`/`globals()`/`locals()`: the namespaces are not static; type nothing.
        self.inert = inert
        self.resolve_dotted = resolve_dotted
        self.b = _Builder(guarded).build(tree)
        self.memo: Dict[Tuple[int, str], Optional[TypeBinding]] = {}
        self.in_progress: set = set()
        self.called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        # Keyword names the script passes ANYWHERE: a class whose `__init__` takes `**kwargs`
        # may have stored one of these on the instance.
        self.keyword_names = {kw.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                              for kw in n.keywords if kw.arg}

    # -- lookup -----------------------------------------------------------------

    def scope_of(self, node) -> Optional[_Scope]:
        return self.b.owner.get(id(node))

    def receiver_type(self, expr, sc: _Scope) -> Optional[TypeBinding]:
        """The class of the object an attribute is read from: a typed name, or an element of
        a typed axes array (`axes[1].boxplot(...)`). Anything else is not a receiver."""
        if isinstance(expr, ast.Name):
            return self.type_of_name(expr.id, sc)
        if isinstance(expr, ast.Subscript):
            return self.type_of_expr(expr, sc)
        return None

    def receiver_label(self, expr) -> str:
        return ast.unparse(expr) if hasattr(ast, "unparse") else "<receiver>"

    def type_of_name(self, name: str, scope: _Scope) -> Optional[TypeBinding]:
        if self.inert:
            return None
        if name in self.dynamic or name in self.assigned_roots or name in self.trusted:
            return None
        sc = scope
        while sc is not None:
            if name in sc.params or name in sc.blocked:
                return None
            if name in sc.bindings:
                return self._typed_in(name, sc)
            sc = sc.parent
            # A method body does not see its class body's names (Python's own rule).
            while sc is not None and sc.is_class and sc is not scope:
                sc = sc.parent
        return None

    def _typed_in(self, name: str, sc: _Scope) -> Optional[TypeBinding]:
        key = (id(sc), name)
        if key in self.memo:
            return self.memo[key]
        if key in self.in_progress:
            return None
        self.in_progress.add(key)
        try:
            result: Optional[TypeBinding] = None
            for binding in sc.bindings[name]:
                t = self._type_of_binding(binding, sc)
                if t is None or (result is not None and t != result):
                    result = None
                    break
                result = t
        finally:
            self.in_progress.discard(key)
        self.memo[key] = result
        return result

    # -- binding shapes ---------------------------------------------------------

    def _type_of_binding(self, binding, sc: _Scope) -> Optional[TypeBinding]:
        kind = binding[0]
        if kind == "value":
            return self.type_of_expr(binding[1], sc)
        if kind == "iter":
            return self._iter_elem(binding[1], sc)
        if kind == "unpack":
            return self._unpack(binding[1], binding[2], binding[3], sc)
        return None

    def _iter_elem(self, it, sc: _Scope) -> Optional[TypeBinding]:
        """The class of what iterating `it` yields, in two shapes only: an element of a typed
        1-D array binding (`for ax in axes`, and `axes.flat` / `.flatten()` / `.ravel()` of any
        array binding), and a call whose curated entry declares `iter_produces`. No library
        is named here: the element class travels in the binding or in the entry."""
        if isinstance(it, ast.Name):
            t = self.type_of_name(it.id, sc)
            return TypeBinding(t.cls, "instance", t.exact) if t and t.kind == "array_1d" else None
        if isinstance(it, ast.Attribute) and isinstance(it.value, ast.Name) and it.attr == "flat":
            t = self.type_of_name(it.value.id, sc)
            return TypeBinding(t.cls, "instance", t.exact) if t and t.kind.startswith("array") else None
        if not isinstance(it, ast.Call):
            return None
        if (isinstance(it.func, ast.Attribute) and isinstance(it.func.value, ast.Name)
                and it.func.attr in ("flatten", "ravel") and not it.args and not it.keywords):
            t = self.type_of_name(it.func.value.id, sc)
            if t and t.kind.startswith("array"):
                return TypeBinding(t.cls, "instance", t.exact)
        if any(kw.arg is None for kw in it.keywords) or any(
                isinstance(a, ast.Starred) for a in it.args):
            return None
        target = self._callable_of(it, sc)
        if target is None:
            return None
        entry = _curated_entry(target[0])
        if entry is None or entry.get("iter_produces") is None:
            return None
        if entry.get("refuse_kwargs") and any(kw.arg in entry["refuse_kwargs"] for kw in it.keywords):
            return None
        cls = entry["iter_produces"]()
        return TypeBinding(cls, "instance", bool(entry.get("exact", False))) if inspect.isclass(cls) else None

    def _unpack(self, value, path, shape, sc: _Scope) -> Optional[TypeBinding]:
        """Only `fig, ax = plt.subplots(...)` and its literal-shape tuple forms are typed."""
        if not isinstance(value, ast.Call) or shape[0] != 2:
            return None
        target = self._callable_of(value, sc)
        if target is None:
            return None
        obj, _receiver = target
        entry = _curated_entry(obj)
        if entry is None or not entry.get("subplots"):
            return None
        dims = _subplots_shape(value)
        if dims is None:
            return None
        nrows, ncols = dims
        fig_cls = entry["produces"]() if entry.get("produces") else None
        axes_cls = entry["elem"]() if entry.get("elem") else None
        if not (inspect.isclass(fig_cls) and inspect.isclass(axes_cls)):
            return None
        if path == [0]:
            return TypeBinding(fig_cls, "instance", True)
        if path[0] != 1:
            return None
        if len(path) == 1:
            if (nrows, ncols) == (1, 1):
                return TypeBinding(axes_cls, "instance", True)
            return TypeBinding(axes_cls, "array_1d" if 1 in (nrows, ncols) else "array_2d", True)
        if len(path) == 2:
            if 1 in (nrows, ncols) and (nrows, ncols) != (1, 1) and shape[1] == nrows * ncols:
                return TypeBinding(axes_cls, "instance", True)
            return None
        if len(path) == 3 and shape[1] == nrows and shape[2] == ncols and nrows > 1 and ncols > 1:
            return TypeBinding(axes_cls, "instance", True)
        return None

    # -- expressions ------------------------------------------------------------

    def type_of_expr(self, expr, sc: _Scope) -> Optional[TypeBinding]:
        if isinstance(expr, ast.Call):
            return self._type_of_call(expr, sc)
        if isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Name):
            t = self.type_of_name(expr.value.id, sc)
            if t is None or not t.kind.startswith("array"):
                return None
            idx = expr.slice
            if t.kind == "array_1d" and not isinstance(idx, (ast.Slice, ast.Tuple)):
                return TypeBinding(t.cls, "instance", True)
            if (t.kind == "array_2d" and isinstance(idx, ast.Tuple) and len(idx.elts) == 2
                    and not any(isinstance(e, ast.Slice) for e in idx.elts)):
                return TypeBinding(t.cls, "instance", True)
        return None

    def _callable_of(self, call: ast.Call, sc: _Scope):
        """`(object, receiver_class_or_None)` for the callee, or None when not knowable."""
        fn = call.func
        if isinstance(fn, ast.Attribute):
            parts: List[str] = []
            cur = fn
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if not isinstance(cur, ast.Name):
                return None
            parts.reverse()
            root = cur.id
            if root in self.trusted:
                obj = self.resolve_dotted(".".join([self.trusted[root]] + parts))
                return (obj, None) if obj is not None else None
            if len(parts) == 1:
                t = self.receiver_type(fn.value, sc)
                if t is None or t.kind != "instance":
                    return None
                static = _static_attr(t.cls, parts[0])
                if static is None:
                    return None
                if isinstance(static, (staticmethod, classmethod)):
                    static = static.__func__
                return (static, t.cls)
            return None
        if isinstance(fn, ast.Name) and fn.id in self.from_aliases and fn.id in self.trusted:
            obj = self.resolve_dotted(self.trusted[fn.id])
            return (obj, None) if obj is not None else None
        return None

    def _literal_dict_keys(self, value, sc: _Scope) -> Optional[set]:
        """The string keys of a dict the script spells out — a `{...}` literal or `dict(k=...)`
        call, inline or through a name bound exactly once to one — else None."""
        if isinstance(value, ast.Name):
            if value.id in self.dynamic or value.id in self.assigned_roots:
                return None
            s = sc
            while s is not None:
                if value.id in s.params or value.id in s.blocked:
                    return None
                if value.id in s.bindings:
                    bs = s.bindings[value.id]
                    if len(bs) != 1 or bs[0][0] != "value":
                        return None
                    return self._literal_dict_keys(bs[0][1], s)
                s = s.parent
            return None
        if isinstance(value, ast.Dict):
            if any(k is None for k in value.keys):
                return None  # `{**other}` inside the literal
            if all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys):
                return {k.value for k in value.keys}
            return None
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "dict" and not value.args
                and "dict" not in self.b.class_names):
            if any(kw.arg is None for kw in value.keywords):
                return None
            return {kw.arg for kw in value.keywords}
        return None

    def _type_of_call(self, call: ast.Call, sc: _Scope) -> Optional[TypeBinding]:
        target = self._callable_of(call, sc)
        if target is None:
            return None
        obj, receiver = target
        # An argument that is a class the SCRIPT defines (`plt.figure(FigureClass=Mine)`): the
        # call may hand back an instance of that class, which no installed package describes.
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            if isinstance(arg, ast.Name) and arg.id in self.b.class_names:
                return None
        entry = _curated_entry(obj)
        if entry is not None:
            if entry.get("subplots") or entry.get("produces") is None:
                return None  # only meaningful through tuple unpacking / iteration
            if entry.get("refuse_kwargs") and (
                    any(kw.arg is None or kw.arg in entry["refuse_kwargs"] for kw in call.keywords)):
                return None
        cls = _produced_class(obj, receiver)
        if not inspect.isclass(cls):
            return None
        # A constructor whose `__init__` takes `**kwargs` (or is not a Python function) and is
        # called with `**unpacked`: it may have stored any name on the instance — unless the
        # unpacked dict is a literal the script itself spells (`params = {"n": 3}`), whose keys
        # then simply join the keyword names the script passes.
        if inspect.isclass(obj) and _init_is_open(obj):
            for kw in call.keywords:
                if kw.arg is not None:
                    continue
                keys = self._literal_dict_keys(kw.value, sc)
                if keys is None:
                    return None
                self.keyword_names |= keys
        # A constructor call, or a curated exact constructor, IS the class; a factory's
        # declared class may be subclassed by what it actually returns.
        exact = inspect.isclass(obj) or (entry is not None and bool(entry.get("exact", True)))
        return TypeBinding(cls, "instance", exact)


# ── judging ──────────────────────────────────────────────────────────────────


def _static_attr(cls, name):
    try:
        return inspect.getattr_static(cls, name)
    except AttributeError:
        return None
    except Exception:  # noqa: BLE001
        return None


def synthesises_attributes(cls) -> bool:
    """True when class-level absence proves nothing about an instance: any MRO class defines
    `__getattr__`, or a PYTHON-level `__getattribute__` / `__new__` (the latter may hand back an
    object of another class entirely)."""
    for klass in getattr(cls, "__mro__", (cls,)):
        if klass is object:
            continue
        d = vars(klass)
        if "__getattr__" in d:
            return True
        if inspect.isfunction(d.get("__getattribute__")) or inspect.isfunction(d.get("__new__")):
            return True
    return False


def _declared_somewhere(cls, attr: str) -> bool:
    """A dataclass/pydantic-style field exists on INSTANCES only: `__annotations__` along the
    MRO say so without the class carrying the attribute."""
    for klass in getattr(cls, "__mro__", (cls,)):
        ann = vars(klass).get("__annotations__")
        if isinstance(ann, dict) and attr in ann:
            return True
    return False


def _subclass_has(cls, attr: str, limit: int = 4000) -> bool:
    """Any LOADED subclass answering `attr` means the object might be one of those."""
    seen: set = set()
    stack = [cls]
    while stack and len(seen) < limit:
        c = stack.pop()
        try:
            subs = c.__subclasses__()
        except Exception:  # noqa: BLE001
            continue
        for s in subs:
            if id(s) in seen:
                continue
            seen.add(id(s))
            try:
                if hasattr(s, attr):
                    return True
            except Exception:  # noqa: BLE001
                return True
            stack.append(s)
    return len(seen) >= limit  # a hierarchy too large to finish is not one to judge


def _open_hierarchy(cls, threshold: int = PLUGIN_HIERARCHY_MIN_SUBCLASSES) -> bool:
    """A class with a family of loaded subclasses is a plugin point (matplotlib's `Axes` and
    its projections, registered as they are used): the class a FACTORY declares then says
    little about the object it hands back, and can name attributes a subclass adds only after
    the check ran. A factory-typed receiver of such a class is never judged."""
    seen: set = set()
    stack = [cls]
    while stack:
        c = stack.pop()
        try:
            subs = c.__subclasses__()
        except Exception:  # noqa: BLE001
            return True
        for s in subs:
            if id(s) not in seen:
                seen.add(id(s))
                if len(seen) >= threshold:
                    return True
                stack.append(s)
    return False


def _subclass_overrides(cls, attr: str, limit: int = 4000) -> bool:
    """Any loaded subclass defining its OWN `attr` may take different keywords."""
    seen: set = set()
    stack = [cls]
    while stack and len(seen) < limit:
        c = stack.pop()
        try:
            subs = c.__subclasses__()
        except Exception:  # noqa: BLE001
            return True
        for s in subs:
            if id(s) in seen:
                continue
            seen.add(id(s))
            if attr in vars(s):
                return True
            stack.append(s)
    return len(seen) >= limit


def _instances_have_no_dict(cls) -> bool:
    """C-level types whose instances carry no `__dict__` (numpy arrays): nothing can be added
    to an instance, so a READ of an absent attribute is as provable as a call."""
    try:
        return getattr(cls, "__dictoffset__", 1) == 0 and "__slots__" not in vars(cls)
    except Exception:  # noqa: BLE001
        return False


_init_cache: Dict[int, Tuple[type, Optional[set], bool]] = {}


def _init_facts(cls) -> Tuple[Optional[set], bool]:
    """`(parameter names of cls.__init__, or None when not introspectable; is it OPEN)`.
    Open = it takes `**kwargs`, or is not a Python function at all (a C-level or extension
    `__init__` whose stores `inspect` cannot see)."""
    got = _init_cache.get(id(cls))
    if got is not None and got[0] is cls:
        return got[1], got[2]
    init = _static_attr(cls, "__init__")
    params: Optional[set] = None
    is_open = not inspect.isfunction(init)
    if inspect.isfunction(init):
        try:
            sig = inspect.signature(init)
            params = {n for n in sig.parameters if n != "self"}
            is_open = _has_varkw(sig)
        except (TypeError, ValueError):
            params, is_open = None, True
    _init_cache[id(cls)] = (cls, params, is_open)
    return params, is_open


def _init_is_open(cls) -> bool:
    return _init_facts(cls)[1]


def _instance_may_carry(cls, attr: str, typer: "Typer") -> bool:
    """Could an INSTANCE of `cls` have `attr` although the class does not? Only for classes
    whose instances carry a `__dict__`: a name ending in `_` (the trailing-underscore
    convention for attributes computed after construction — an estimator's fitted values), a
    parameter of `__init__` (stored constructor arguments), or — for an `__init__` that takes
    `**kwargs` or is not a Python function — a keyword the script passes anywhere."""
    if _instances_have_no_dict(cls):
        return False
    if attr.endswith("_"):
        return True
    params, is_open = _init_facts(cls)
    if params is not None and attr in params:
        return True
    return is_open and attr in typer.keyword_names


def absent_on_typed_receivers(typer: Typer, max_findings: int = 6) -> List[AbsentFinding]:
    """`name.attr` where `name` is typed and `attr` is provably not on the class.

    Silent when: the class synthesises attributes; the attr is a dunder/private name; the script
    defines a class of the same name, or assigns that attribute name anywhere; the attr is a
    declared (instance-only) field; any loaded subclass has it; the attr is only READ on a
    class whose instances can carry a `__dict__` (`__init__` may put anything there), or is a
    name such an instance may carry although its class does not (`_instance_may_carry`); the
    access is try-guarded.
    """
    out: List[AbsentFinding] = []
    seen: set = set()
    for node in ast.walk(typer.tree):
        if not isinstance(node, ast.Attribute) or id(node) in typer.guarded:
            continue
        if not isinstance(node.value, (ast.Name, ast.Subscript)) or node.attr.startswith("_"):
            continue
        sc = typer.scope_of(node)
        if sc is None:
            continue
        t = typer.receiver_type(node.value, sc)
        if t is None or t.kind != "instance":
            continue
        cls, attr = t.cls, node.attr
        key = (id(cls), attr)
        if key in seen:
            continue
        called = id(node) in typer.called
        if not called and not _instances_have_no_dict(cls):
            continue
        if attr in typer.assigned_attrs or _instance_may_carry(cls, attr, typer):
            continue
        if cls.__name__ in typer.b.class_names or synthesises_attributes(cls):
            continue
        if not t.exact and _open_hierarchy(cls):
            continue
        try:
            if hasattr(cls, attr):
                continue
        except Exception:  # noqa: BLE001
            continue
        if _declared_somewhere(cls, attr) or _subclass_has(cls, attr):
            continue
        seen.add(key)
        out.append(AbsentFinding(cls, attr, typer.receiver_label(node.value), called))
        if len(out) >= max_findings:
            break
    return out


def _plain_callable(static) -> bool:
    """A function or a C-level method descriptor — never a class, property, accessor,
    classmethod/staticmethod or an arbitrary callable object."""
    if inspect.isclass(static) or isinstance(static, (property, classmethod, staticmethod)):
        return False
    return inspect.isfunction(static) or inspect.ismethoddescriptor(static)


def _has_varkw(sig: inspect.Signature) -> bool:
    return any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())


_judged_cache: Dict[int, tuple] = {}


def _judged_params(static) -> Optional[Tuple[inspect.Signature, set, Optional[str], object]]:
    """`(signature, valid_keyword_names, forwarded_to, judged_object)` or None when the callable's
    keyword set is not a static fact: a `**kwargs` on EITHER the wrapper or the wrapped signature
    means a keyword may be consumed frames deeper. Memoised per object (pinned in the cache,
    so an id is never reused): a loop of ten thousand `.sum(axis=0)` calls asks once."""
    got = _judged_cache.get(id(static))
    if got is not None and got[0] is static:
        return got[1]
    ans = _judged_params_uncached(static)
    _judged_cache[id(static)] = (static, ans)
    return ans


def _judged_params_uncached(static) -> Optional[Tuple[inspect.Signature, set, Optional[str], object]]:
    entry = _curated_entry(static)
    if entry is not None and entry.get("forward_to"):
        target = _resolve_path(entry["forward_to"])
        if target is None:
            return None
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            return None
        if _has_varkw(sig):
            return None
        names = {n for n in sig.parameters if n not in entry["forward_hides"]}
        return sig, names | set(entry["forward_accepts"]), entry["forward_to"], target
    if not _plain_callable(static):
        return None
    try:
        raw = inspect.signature(static, follow_wrapped=False)
        sig = inspect.signature(static)
    except (TypeError, ValueError):
        return None
    if _has_varkw(raw) or _has_varkw(sig):
        return None
    return sig, set(sig.parameters), None, static


def kwarg_mismatch_on_typed_receivers(typer: Typer, max_findings: int = 6) -> List[KwargFinding]:
    """`name.method(k=...)` where `name` is typed and `k` is provably not accepted — plus the
    curated forwarders, whichever way they are reached.

    Silent when: the attribute is not a plain function / method descriptor (a class, property or
    accessor is called through machinery the signature does not show); either signature accepts
    `**kwargs`; the call unpacks `**`; the script defines a class of the same name.
    """
    out: List[KwargFinding] = []
    seen: set = set()
    for node in ast.walk(typer.tree):
        if not isinstance(node, ast.Call) or id(node) in typer.guarded:
            continue
        if not node.keywords or any(kw.arg is None for kw in node.keywords):
            continue
        sc = typer.scope_of(node)
        if sc is None:
            continue
        fn = node.func
        label: Optional[str] = None
        static = None
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, (ast.Name, ast.Subscript)):
            root = fn.value.id if isinstance(fn.value, ast.Name) else None
            if root in typer.trusted:
                static = typer.resolve_dotted(typer.trusted[root] + "." + fn.attr)
                if static is None or _curated_entry(static) is None:
                    continue  # the plain module-level path is the signature probe's business
                label = typer.trusted[root] + "." + fn.attr
            else:
                t = typer.receiver_type(fn.value, sc)
                if t is None or t.kind != "instance" or t.cls.__name__ in typer.b.class_names:
                    continue
                if synthesises_attributes(t.cls):
                    continue
                if not t.exact and (_open_hierarchy(t.cls) or _subclass_overrides(t.cls, fn.attr)):
                    continue
                if fn.attr in typer.assigned_attrs or _instance_may_carry(t.cls, fn.attr, typer):
                    continue  # the instance may carry its own callable under that name
                static = _static_attr(t.cls, fn.attr)
                if static is None:
                    continue
                label = f"{t.cls.__name__}.{fn.attr}"
        elif isinstance(fn, ast.Name) and fn.id in typer.from_aliases and fn.id in typer.trusted:
            static = typer.resolve_dotted(typer.trusted[fn.id])
            if static is None or _curated_entry(static) is None:
                continue
            label = typer.trusted[fn.id]
        else:
            continue
        judged = _judged_params(static)
        if judged is None:
            continue
        sig, valid, forwarded_to, obj = judged
        unknown = [kw.arg for kw in node.keywords if kw.arg not in valid]
        if not unknown:
            continue
        key = (label, tuple(unknown))
        if key in seen:
            continue
        seen.add(key)
        out.append(KwargFinding(label, obj, sig, unknown, forwarded_to))
        if len(out) >= max_findings:
            break
    return out


__all__ = [
    "Typer", "TypeBinding", "AbsentFinding", "KwargFinding",
    "absent_on_typed_receivers", "kwarg_mismatch_on_typed_receivers",
    "synthesises_attributes", "load_extensions", "PLUGIN_HIERARCHY_MIN_SUBCLASSES",
]
