"""The harness is general: no prompt, template, injected note, report text or log message names a science
domain, a dataset or a domain library. cmbagent_lg also runs cancer-genomics and molecular-property fleets, so
package names reach a model only from the run's own context (PlanContext fields the caller sets, such as
`hardware_constraints` or `input_roots`) or from deterministic introspection of the installed environment, and
every rule is phrased generically.

Scanned:
  * every string value of every template (`cmbagent_lg/**/templates/*.yaml`);
  * every string literal in `cmbagent_lg/**/*.py` that is not a docstring: module-level prompt strings, the
    fixed text of note builders and retry or bounce reports, f-string parts, log messages, tool descriptions given
    as literals and the introspection snippets;
  * the text a model reads that Python builds from docstrings: the JSON schema of every pydantic model the
    package defines (what `with_structured_output` sends, class docstrings and `Field` descriptions included),
    the per-run plan models, and the researcher's tools as `bind_tools` sends them;
  * the note builders, called on synthetic records.
Other comments and docstrings are not text a model reads and are not scanned.

Whitespace is collapsed before matching, so a term split across a line break in a YAML block or a string
continuation is still found. `_ALLOWED` holds an exact text only where a test below proves the use is generic.

    PYTHONPATH=. python -m pytest tests/test_prompt_neutrality.py
"""

import ast
import importlib
import json
import os
import pkgutil
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PKG = Path(__file__).resolve().parent.parent / "cmbagent_lg"

# Domain, dataset and domain-library terms, matched case-insensitively on whitespace-collapsed text. Words are
# joined by `_SEP` so "power spectrum", "power_spectrum", "power-spectrum" and a line-wrapped "power\nspectrum"
# all match; `(?<![a-z])` / `(?![a-z])` keep look-alikes ("Cambridge", "halogen", "general") out.
_SEP = r"[\s_-]*"
DENYLIST = {
    # cosmology and astrophysics
    "flamingo": r"flamingo",
    "camb": r"(?<![a-z])(?:py)?camb(?!ridge|odia|rian|ric)",
    "class_sz": r"class" + _SEP + r"sz",
    "classy": r"(?<![a-z])classy",
    "colossus": r"colossus",
    "healpy": r"healpy",
    "healpix": r"healpix",
    "soap": r"(?<![a-z])soap(?![a-z])",
    "tinker": r"tinker(?!ing|ed|s\b)",
    "redshift": r"redshift",
    "snapshot": r"snapshot",
    "halo": r"(?<![a-z])(?:sub)?halo(?:e?s)?(?![a-z])",
    "cosmolog": r"cosmolog",
    "power spectrum": r"power" + _SEP + r"spectr",
    "sigma8": r"sigma" + _SEP + r"8",
    "hmcode": r"hmcode",
    "n_eff": r"(?<![a-z])n_?eff(?![a-z])",
    "limber": r"limber",
    "compton": r"compton",
    "sz effect": r"(?<![a-z[])[kt]?sz(?![a-z\]])",
    "cmb": r"(?<![a-z])cmb(?![a-z])",
    "planck": r"planck",
    "lensing": r"lensing",
    "galaxy": r"galax",
    "neutrino": r"neutrino",
    "hubble": r"hubble",
    "dark energy": r"dark" + _SEP + r"energy",
    "dark matter": r"dark" + _SEP + r"matter",
    "baryon": r"baryon",
    "lightcone": r"light" + _SEP + r"cone",
    "nfw": r"(?<![a-z])nfw(?![a-z])",
    "mass function": r"(?<!probability )(?<!probability_)(?<!probability-)mass" + _SEP + r"functions?",
    "mpc": r"(?<![a-z])h?mpc(?![a-z])",
    "msun": r"(?<![a-z])m_?(?:sun|sol|odot)(?![a-z])",
    "astro": r"astro(?:py|nom|phys)",
    "cobaya": r"cobaya",
    "getdist": r"getdist",
    "ccl": r"pyccl|(?<![a-z])ccl(?![a-z])",
    "survey": r"(?<![a-z])(?:sdss|desi|lsst|euclid|des y\d)(?![a-z])",
    # survival analysis and clinical
    "fine-gray": r"fine" + _SEP + r"gray",
    "kaplan-meier": r"kaplan" + _SEP + r"meier",
    "cox": r"(?<![a-z])cox(?![a-z])",
    "competing risks": r"competing" + _SEP + r"risks?",
    "survival": r"(?<![a-z])survival(?![a-z])",
    "hazard": r"(?<![a-z])hazards?(?![a-z])",
    "lifelines": r"lifelines",
    "scikit-survival": r"scikit" + _SEP + r"surv|sksurv",
    "metabric": r"metabric",
    "tcga": r"tcga",
    "cohort": r"cohort",
    "patient": r"(?<![a-z])patients?(?![a-z])",
    "clinical": r"clinical",
    "cancer": r"cancer",
    "tumour": r"tumou?r",
    # genomics and molecular
    "genomic": r"genom",
    "gene": r"(?<![a-z])genes?(?![a-z])",
    "rna/dna": r"(?<![a-z])(?:sc)?(?:rna|dna)(?![a-z])",
    "single cell": r"single" + _SEP + r"cell",
    "scanpy": r"scanpy|anndata|h5ad",
    "protein": r"protein",
    "molecule": r"molecul",
    "smiles": r"(?-i:SMILES)",
    "qm9": r"(?<![a-z])qm9(?![a-z])",
    "rdkit": r"rdkit",
    "chembl": r"chembl|pubchem",
}
_PATTERNS = {term: re.compile(rx, re.I) for term, rx in DENYLIST.items()}

# (file, term, exact text) proven generic by a test below. Keyed on the exact text, so another use of the same
# term in the same file is still a hit.
_ALLOWED = {
    # The attempt manifest's directory snapshot: only its own function names in `__all__`, never prose. Proven by
    # `test_allowed_uses_are_generic`.
    ("cmbagent_lg/self_debug/attempt_manifest.py", "snapshot", "snapshot_tree"),
    ("cmbagent_lg/self_debug/attempt_manifest.py", "snapshot", "diff_snapshots"),
}


def _norm(text: str) -> str:
    return " ".join(text.split())


def _terms(text: str) -> set:
    t = _norm(text)
    return {term for term, pat in _PATTERNS.items() if pat.search(t)}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


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


def _modules():
    mods = []
    for info in pkgutil.walk_packages([str(PKG)], "cmbagent_lg."):
        mods.append(importlib.import_module(info.name))   # an import error fails the scan: nothing is skipped
    return mods


def _schema_texts():
    """`(where, text)` of what the package's structured-output schemas and tools send to a model."""
    import pydantic
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from cmbagent_lg.context import PlanContext
    from cmbagent_lg.planning.nodes import _build_dynamic_plan_model
    from cmbagent_lg.researcher.react_researcher import _make_tools

    models = {}
    for mod in _modules():
        for v in vars(mod).values():
            if (isinstance(v, type) and issubclass(v, pydantic.BaseModel) and v.__module__.startswith("cmbagent_lg")):
                models[f"{v.__module__}.{v.__qualname__}"] = v
    assert len(models) >= 10, sorted(models)
    out = []
    for name, cls in sorted(models.items()):
        out.append((f"schema {name}", json.dumps(cls.model_json_schema())))
    dyn = _build_dynamic_plan_model([("engineer", "writes code"), ("researcher", "writes reports")])
    out.append(("schema DynPlan", json.dumps(dyn.model_json_schema())))
    tools = _make_tools(None, PlanContext(main_task="t", vlm_enabled=True), [])
    assert {t.__name__ for t in tools} >= {"list_workspace", "read_file", "view_image"}
    for t in tools:
        out.append((f"tool {t.__name__}", json.dumps(convert_to_openai_tool(t))))
    return out


def _all_hits() -> dict:
    """`{(file, term, text): [where, ...]}` over the templates, the sources and the schemas."""
    hits: dict = {}
    templates = sorted(PKG.rglob("templates/*.yaml"))
    assert templates, "no templates found"
    for path in templates:
        for text in _strings(yaml.safe_load(path.read_text())):
            for term in _terms(text):
                hits.setdefault((_rel(path), term, text), []).append(_norm(text)[:100])
    for path in sorted(PKG.rglob("*.py")):
        for line, text in _source_strings(path):
            for term in _terms(text):
                hits.setdefault((_rel(path), term, text), []).append(f"line {line}: {_norm(text)[:100]!r}")
    for where, text in _schema_texts():
        for term in _terms(text):
            pat = _PATTERNS[term]
            m = pat.search(_norm(text))
            hits.setdefault((where, term, text), []).append(_norm(text)[max(0, m.start() - 60):m.end() + 40])
    return hits


def test_no_domain_terms_in_prompts_notes_or_messages():
    hits = _all_hits()
    new = {k: v for k, v in hits.items() if k not in _ALLOWED}
    assert not new, "domain-specific text in the harness:\n" + "\n".join(
        f"{f} [{t}]: {w}" for (f, t, _x), ws in sorted(new.items()) for w in ws)


def test_the_allowed_list_is_current():
    # An allowed text that has been removed must leave the list too.
    hits = _all_hits()
    stale = sorted(k for k in _ALLOWED if k not in hits)
    assert not stale, f"no longer found, remove from the list: {stale}"


def test_allowed_uses_are_generic():
    import cmbagent_lg.self_debug.attempt_manifest as am
    for path, _term, text in _ALLOWED:
        assert path == "cmbagent_lg/self_debug/attempt_manifest.py"
        assert text.isidentifier() and callable(getattr(am, text, None)) and text in am.__all__, text


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
    # The matcher catches the terms in ordinary prose, in their variant spellings and across a line break, and
    # leaves look-alikes alone.
    assert _terms("Use CAMB to compute the matter power spectrum at redshift 1") >= {"camb", "power spectrum",
                                                                                    "redshift"}
    assert _terms("a file under another snapshot directory") == {"snapshot"}
    for text, term in (("haloes", "halo"), ("subhaloes", "halo"), ("halo_mass", "halo"), ("import pycamb", "camb"),
                       ("power\nspectrum", "power spectrum"), ("dark\n    energy", "dark energy"),
                       ("fine-\ngray", "fine-gray"), ("Kaplan-Meier", "kaplan-meier"), ("a Cox model", "cox"),
                       ("the cohort", "cohort"), ("SMILES strings", "smiles"), ("10 Mpc/h", "mpc"),
                       ("M_sun", "msun"), ("the kSZ signal", "sz effect"), ("halo mass function", "mass function"),
                       ("import astropy", "astro"), ("TCGA-BRCA", "tcga"), ("scRNA-seq", "rna/dna")):
        assert term in _terms(text), (text, _terms(text))
    assert not _terms("Cambridge; classify the classes; a halogen lamp; behalf; tinkering; general; generic; "
                      "it survives; a probability mass function; smiles; sizes; patiently; the soapstone")
    # Docstrings are left out, every other literal is read, f-string parts included.
    src = ('"""Module doc about the CMB."""\nX = "plain"\ndef f(z):\n    """Doc on halos."""\n'
           '    return f"at redshift {z}"\n')
    tmp = Path(__file__).with_name("_neutrality_probe.py")
    tmp.write_text(src)
    try:
        assert [text for _l, text in _source_strings(tmp)] == ["plain", "at redshift "]
    finally:
        tmp.unlink()


def test_model_facing_docstrings_are_scanned():
    # A tool docstring and a schema docstring reach the model; the scan reads both.
    texts = dict(_schema_texts())
    assert "List every file the analysis has produced" in texts["tool list_workspace"]
    assert any(k.endswith(".ImageReview") for k in texts)
    from pydantic import BaseModel, Field
    from langchain_core.utils.function_calling import convert_to_openai_tool

    class Probe(BaseModel):
        """Fit the halo model."""
        x: int = Field(description="the redshift")

    def probe_tool() -> str:
        """Read the snapshot."""
        return ""

    assert _terms(json.dumps(Probe.model_json_schema())) == {"halo", "redshift"}
    assert _terms(json.dumps(convert_to_openai_tool(probe_tool))) == {"snapshot"}
