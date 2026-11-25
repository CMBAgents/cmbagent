"""The harness is general: no prompt, template, injected note, report text or log message names a science
domain, a dataset or a domain library. cmbagent_lg also runs cancer-genomics and molecular-property fleets, so
package names reach a model only from the run's own context (PlanContext fields the caller sets, such as
`hardware_constraints` or `input_roots`) or from deterministic introspection of the installed environment, and
every rule is phrased generically.

Scanned: every string value of every template (`cmbagent_lg/**/templates/*.yaml`), and every string literal in
`cmbagent_lg/**/*.py` that is not a docstring: module-level prompt strings, the fixed text of note builders and
retry or bounce reports, f-string parts, log messages and the introspection snippets. The note builders are also
called on synthetic records and their output scanned. Comments and docstrings are not text a model reads and are
not scanned.

`_ALLOWED` holds a term only where a test below proves the use is generic. `_PRE_EXISTING` holds the hits older
than 21 Nov 2026, left as they are for a decision (they are reported, not changed); any new hit fails.

    PYTHONPATH=. python -m pytest tests/test_prompt_neutrality.py
"""

import ast
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PKG = Path(__file__).resolve().parent.parent / "cmbagent_lg"

# Domain, dataset and domain-library terms. Case-insensitive.
DENYLIST = {
    "flamingo": r"flamingo",
    "camb": r"\bcamb(?!ridge)",
    "class_sz": r"class_?sz",
    "classy": r"\bclassy",
    "colossus": r"colossus",
    "healpy": r"healpy",
    "healpix": r"healpix",
    "soap": r"\bsoap\b",
    "tinker": r"tinker",
    "redshift": r"redshift",
    "snapshot": r"snapshot",
    "halo": r"\b(?:sub)?halos?\b",
    "cosmolog": r"cosmolog",
    "power spectrum": r"power[ _-]?spectr",
    "sigma8": r"sigma[ _]?8",
    "hmcode": r"hmcode",
    "n_eff": r"\bn_?eff\b",
    "limber": r"limber",
    "compton": r"compton",
    "tsz": r"\btsz\b",
    "cmb": r"\bcmb\b",
    "planck": r"planck",
    "lensing": r"lensing",
    "galaxy": r"galax",
    "neutrino": r"neutrino",
    "hubble": r"hubble",
    "dark energy": r"dark[ _-]?energy",
    "baryon": r"baryon",
    "lightcone": r"light[ _-]?cone",
    "fine-gray": r"fine[ _-]gray",
    "lifelines": r"lifelines",
    "scikit-survival": r"scikit[ _-]surv|sksurv",
    "metabric": r"metabric",
    "qm9": r"\bqm9\b",
    "rdkit": r"rdkit",
    "cancer": r"cancer",
    "tumour": r"tumou?r",
    "genomic": r"genom",
    "molecule": r"molecul",
}
_PATTERNS = {term: re.compile(rx, re.I) for term, rx in DENYLIST.items()}

# (file, term) pairs proven generic by a test below.
_ALLOWED = {
    # The attempt manifest's directory snapshot: only its own function names (`snapshot_tree`,
    # `diff_snapshots` in `__all__`), never prose. Proven by `test_allowed_uses_are_generic`.
    ("cmbagent_lg/self_debug/attempt_manifest.py", "snapshot"),
}

# Hits older than 21 Nov 2026, reported and left unchanged for a decision.
_PRE_EXISTING = {
    # The bundled-example-dataset loaders the static pre-flight refuses (`demo_dataset_offender`): names it
    # looks for in the script's code; the message names only the loader the script itself called.
    ("cmbagent_lg/self_debug/static_preflight.py", "lifelines"),
    ("cmbagent_lg/self_debug/static_preflight.py", "cancer"),
    ("cmbagent_lg/self_debug/static_preflight.py", "scikit-survival"),
    # The step evaluator's example of a checkable infeasibility justification.
    ("cmbagent_lg/self_debug/templates/step_evaluator.yaml", "fine-gray"),
    ("cmbagent_lg/self_debug/templates/step_evaluator.yaml", "lifelines"),
    ("cmbagent_lg/self_debug/templates/step_evaluator.yaml", "scikit-survival"),
}


def _terms(text: str) -> set:
    return {term for term, pat in _PATTERNS.items() if pat.search(text)}


def _yaml_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _yaml_strings(k)
            yield from _yaml_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _yaml_strings(v)


def _docstring_ids(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _source_strings(path: Path):
    """`(line, text)` of every string literal in `path` that is not a docstring."""
    tree = ast.parse(path.read_text())
    docs = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def _rel(path: Path) -> str:
    return path.relative_to(PKG.parent).as_posix()


def _all_hits() -> dict:
    """`{(file, term): [where, ...]}` over the templates and the sources."""
    hits: dict = {}
    templates = sorted(PKG.rglob("templates/*.yaml"))
    assert templates, "no templates found"
    for path in templates:
        for text in _yaml_strings(yaml.safe_load(path.read_text())):
            for term in _terms(text):
                hits.setdefault((_rel(path), term), []).append(text[:80])
    for path in sorted(PKG.rglob("*.py")):
        for line, text in _source_strings(path):
            for term in _terms(text):
                hits.setdefault((_rel(path), term), []).append(f"line {line}: {text[:80]!r}")
    return hits


def test_no_domain_terms_in_prompts_notes_or_messages():
    hits = _all_hits()
    new = {k: v for k, v in hits.items() if k not in _ALLOWED and k not in _PRE_EXISTING}
    assert not new, "domain-specific text in the harness:\n" + "\n".join(
        f"{f} [{t}]: {w}" for (f, t), ws in sorted(new.items()) for w in ws)


def test_the_pre_existing_list_is_current():
    # A pre-existing hit that has been removed must leave the list too, so the list never hides a new one.
    hits = _all_hits()
    stale = sorted(k for k in _PRE_EXISTING | _ALLOWED if k not in hits)
    assert not stale, f"no longer found, remove from the lists: {stale}"


def test_allowed_uses_are_generic():
    import cmbagent_lg.self_debug.attempt_manifest as am
    path = PKG / "self_debug" / "attempt_manifest.py"
    found = [text for _line, text in _source_strings(path) if _terms(text) & {"snapshot"}]
    assert found and all(t.isidentifier() and callable(getattr(am, t, None)) and t in am.__all__
                         for t in found), found


def test_note_builders_on_synthetic_records_are_neutral():
    from cmbagent_lg.self_debug.input_search import render_search_note, summary_line, terminal_note

    found = {"path": "/in/run_a/x/out_007.dat", "basename": "out_007.dat", "found": ["/in/run_b/x/out_007.dat"],
             "n_found": 1, "n_strong": 0, "strong": False, "veto": "strong", "roots": ["/in"], "entries_seen": 9}
    close = {"path": "/in/run_a/x/out_007.dat", "basename": "out_007.dat", "found": [], "close": ["Out_007.dat"],
             "close_parent": "/in/run_a/x", "close_hint": ["out_008.dat"], "n_found": 0, "veto": "close",
             "roots": ["/in"], "entries_seen": 9, "truncated": "entries"}
    texts = []
    for rec in (found, close):
        texts += [render_search_note(rec), summary_line(rec), terminal_note(rec)]
    assert any("DIFFERENT input" in t for t in texts)
    for t in texts:
        assert not _terms(t), (t, _terms(t))


def test_the_scan_sees_what_it_should():
    # The matcher catches the terms in ordinary prose and leaves look-alikes alone.
    assert _terms("Use CAMB to compute the matter power spectrum at redshift 1") >= {"camb", "power spectrum",
                                                                                    "redshift"}
    assert _terms("a file under another snapshot directory") == {"snapshot"}
    assert not _terms("Cambridge; classify the classes; a halogen lamp; behalf; tinkle")
    # Docstrings are left out, every other literal is read, f-string parts included.
    src = ('"""Module doc about the CMB."""\nX = "plain"\ndef f(z):\n    """Doc on halos."""\n'
           '    return f"at redshift {z}"\n')
    tmp = Path(__file__).with_name("_neutrality_probe.py")
    tmp.write_text(src)
    try:
        assert [text for _l, text in _source_strings(tmp)] == ["plain", "at redshift "]
    finally:
        tmp.unlink()
