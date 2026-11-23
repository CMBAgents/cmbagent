"""What one attempt wrote: a recursive before/after snapshot of the run directory, plus a
check of every file path the attempt's stdout names.

The executor used to snapshot only the top level of `data/`, so a file written anywhere
else (`answer.json` at the root of the run directory, `work/spectra.npz`,
`data/plots/x.png`) was invisible: the step evaluator was told "no output files were
produced" for steps whose files existed, and rejected them. Measured on the FLAMINGO
calibration set (cases 4, 6, 8, 15), where the task asks for `answer.json` at an absolute
path in the run directory and plans write under `work/`.

One representation, shared by every consumer (the step evaluator prompt,
`output_postcondition`, the image reviewer, `infeasibility_check`, the workspace
listing):

  `data_manifest`       list of `{"path", "bytes", "modified", "change"}` — EVERY file
                        written or modified by THIS attempt (bounded only by the walk's
                        file cap, never cut for display). `path` is relative to the work
                        directory (`answer.json`, `work/spectra.npz`, `data/plots/x.png`);
                        a file outside it that stdout named and this attempt wrote is
                        listed by its absolute path with `"source": "stdout"`. The field
                        keeps its old name: every entry the old top-level scan produced
                        (`data/<name>`) is produced unchanged.
  `data_manifest_info`  `{"scope", "complete", "incomplete_reason", "omitted",
                        "symlinked_dirs", "partial_dirs", "stdout_paths", "started_ns"}` —
                        how the listing was made and whether it is whole, which directories
                        it could not list file by file, and the classification of every
                        path stdout named: `written` / `pre_existing` / `missing` /
                        `not_found` (a bare file name found nowhere).

THREE KINDS OF FILE, for the evaluator. A step's attempts share one run directory, and a
resumable script (one that skips work an earlier attempt already saved) legitimately
leaves that work untouched. So beside "written by THIS attempt" the evaluator is told
which of the other files were written by an EARLIER ATTEMPT OF THE SAME STEP and left
unchanged ("carried over", with the attempt number), and which were on disk before the
step began (an earlier step's output or a provided input: "pre-existing"). The step's
history (the snapshot taken right before the step's first attempt ran, and which attempt
last wrote each file, with its signature) lives in `logs/step_N_history.json`, read and
written through `StepHistory`; graph state holds only a small reference to it
(`step_history`). The file outlives the graph invoke, so a step restarted after a kill
still tells its killed run's files ("an earlier run of this same step") from
pre-existing ones:

  `data_manifest_info["carried_over"]`  `{"count", "bytes", "listed", "groups",
                        "more"}` — the carried-over files, already shortened for the
                        prompt (at most `MAX_CARRIED_LISTED` entries; a longer set is
                        summarised per directory and attempt), so the state holds only
                        what the rendering needs.
  stdout checks         a stdout-named file another attempt of this step wrote is
                        `carried_over` (with `"attempt"`); a glob (`partial_results/*.json`)
                        or a directory (`results/`) inside the run directory is counted
                        by class against the walk (`"kind": "glob" | "directory"`), never
                        listed file by file.

`data_manifest` itself keeps its meaning ("written or modified by THIS attempt"):
`output_postcondition` still rejects a declared output only an earlier attempt wrote
(see its docstring). Carried-over files are information for the evaluator, not proof
that a declared output was freshly written.

Only the evaluator's RENDERING is shortened (`MAX_RENDERED` entries, chosen so root
files, stdout-named files, declared outputs and figures come first, the rest collapsed per
directory). The checks read the whole list, so a step that also writes 250 chunk files
does not lose its `data/summary.csv` to a display cap.

SEMANTICS: "written or modified by THIS attempt". The snapshot is taken immediately
before the script starts and again after it ends, and a file is listed only when it is
new or its (size, mtime_ns, ctime_ns, inode) changed, so a stale file an earlier step or
attempt left on disk is never listed — the metabric case in `output_postcondition`'s
docstring. ctime is part of the key because userland cannot set it: `shutil.copy2`, tar
or zip extraction, `rsync -a` and `wget` preserve or backdate mtime, and an atomic
rename changes the inode.

BOUNDED: no symlink is followed (except a top-level `data/` that is itself a symlink,
which the old top-level scan read through), the walk stops at `max_files` files,
`max_depth` directory levels and a budget of directory entries read, and a directory
with more than `max_dir_entries` entries is not listed file by file (it is recorded in
`partial_dirs` and the walk goes on with the others, so one huge directory does not use
up the cap for the rest of the tree). A hit global cap marks the listing incomplete; a
path under a partial or symlinked directory is "unseen". No consumer reads an unseen or
absent file as "not written" on that basis alone. Engine-owned directories
(`ENGINE_DIRS`, top level only) and caches/hidden directories are never entered.

TIMES: "since the attempt started" is measured against a sentinel file the executor
touches in `logs/` right before the script starts (its filesystem timestamp, so a
networked filesystem whose server clock is skewed from the engine's is compared on its
own clock), or the engine clock, whichever is earlier; `MTIME_SLACK_NS` of slack covers
coarse-granularity filesystems. A file's change time is `max(mtime, ctime)`.

stdout paths are only ever `stat()`-ed, never opened: the classification is "is it in
this attempt's snapshot diff" when the walk covered it, otherwise "was it changed after
the attempt started". A relative token that does not resolve against the script's cwd is
matched by path suffix against the walk (`spectrum.png` printed for `data/spectrum.png`,
or a path printed after `os.chdir("work")`).

REMOTE (`CMBAGENT_LG_REMOTE_EXECUTOR`): the data lives guest-side and the guest reports
the top level of its `data/` exactly as before; `remote_info` marks that scope so the
evaluator is told what the listing covers, and no engine-side check runs.
"""

from __future__ import annotations

import os
import re
import stat as _stat
import time
from collections import deque
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Directories at the TOP LEVEL of the work dir that the engine itself writes:
#   codebase/  step scripts and run logs (self_debug executor)
#   logs/      verdicts, manifests, api cache, tool transcripts
#   planning/  the plan (persistence.save_final_plan)
#   reports/   researcher write-ups (researcher / react_researcher)
#   chats/     legacy cmbagent chat transcripts, when a run dir is shared with it
ENGINE_DIRS = frozenset({"codebase", "logs", "planning", "reports", "chats"})
# Top-level files the engine writes (persistence.save_langfuse_trace_id).
ENGINE_FILES = frozenset({"langfuse_trace_id.txt"})
# Never entered at any depth: caches and tool scratch/tracking directories. Hidden
# directories (".git", ".venv", ".cache", ...) are skipped by the leading-dot rule too.
CACHE_DIRS = frozenset({"__pycache__", ".cache", ".ipynb_checkpoints", "node_modules",
                        ".pytest_cache", ".mypy_cache", ".git", "dask-worker-space",
                        "wandb", "mlruns", "lightning_logs"})

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_DIR_ENTRIES = 1000
# Directory entries read in one walk, as a multiple of max_files (partial directories
# are read up to their cap and count against it).
_ENTRY_BUDGET_FACTOR = 4
_MAX_SYMLINKED = 500
# Entries the evaluator is shown; the rest are summarised per directory.
MAX_RENDERED = 40
_MAX_RENDERED_GROUPS = 15
MAX_STDOUT_PATHS = 25
# Globs and directories named in stdout that are counted by class.
MAX_STDOUT_GROUPS = 10
# Glob/directory tokens resolved at most, to find those MAX_STDOUT_GROUPS.
_GROUP_TRIES = 100
# Carried-over files listed one by one; a longer set is summarised per directory.
MAX_CARRIED_LISTED = 20
# When summarised, at most this many stdout-named or root files are still listed.
_MAX_CARRIED_KEPT = 10
# Unique path-shaped candidates considered before choosing which to check.
_MAX_CANDIDATES = 2000
# Head + tail of stdout scanned for paths.
_STDOUT_SCAN_CHARS = 200_000
MTIME_SLACK_NS = 2_000_000_000

# rel path -> (size, mtime_ns, ctime_ns, inode)
Snapshot = Dict[str, Tuple[int, int, int, int]]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    return default


def walk_limits() -> Tuple[int, int, int]:
    """(max_files, max_depth, max_dir_entries): the module defaults, overridable per
    fleet with `CMBAGENT_LG_MANIFEST_MAX_FILES` / `_MAX_DEPTH` / `_MAX_DIR_ENTRIES`."""
    return (_env_int("CMBAGENT_LG_MANIFEST_MAX_FILES", DEFAULT_MAX_FILES),
            _env_int("CMBAGENT_LG_MANIFEST_MAX_DEPTH", DEFAULT_MAX_DEPTH),
            _env_int("CMBAGENT_LG_MANIFEST_MAX_DIR_ENTRIES", DEFAULT_MAX_DIR_ENTRIES))


def changed_ns(st) -> int:
    """When a file was last written or had its inode changed: `max(mtime, ctime)`."""
    return max(st.st_mtime_ns, st.st_ctime_ns)


class WalkResult:
    """A bounded snapshot of a directory tree: rel posix path -> (size, mtime_ns,
    ctime_ns, inode)."""

    __slots__ = ("files", "truncated", "symlinked_dirs", "partial_dirs")

    def __init__(self):
        self.files: Snapshot = {}
        # None when the whole tree was covered, else why the walk stopped short.
        self.truncated: Optional[str] = None
        # Relative paths of symlinked directories that were not entered.
        self.symlinked_dirs: List[str] = []
        # Relative paths of directories with more than `max_dir_entries` entries, which
        # were not listed file by file ("." for the root itself).
        self.partial_dirs: List[str] = []


def _skip_dir(name: str, rel_parent: str) -> bool:
    if name.startswith(".") or name in CACHE_DIRS:
        return True
    return rel_parent == "" and name in ENGINE_DIRS


def _skip_file(name: str, rel_parent: str) -> bool:
    # Hidden files (.DS_Store, .nfsXXXX silly renames, .~lock.* office locks), office
    # owner files (~$x.xlsx), bytecode, and the engine's own root files.
    if name.startswith(".") or name.startswith("~$") or name.endswith(".pyc"):
        return True
    return rel_parent == "" and name in ENGINE_FILES


def snapshot_tree(root, max_files: Optional[int] = None, max_depth: Optional[int] = None,
                  max_dir_entries: Optional[int] = None) -> WalkResult:
    """Walk `root` (breadth-first, sorted within each directory) and record every regular
    file outside the excluded directories. Never raises: an unreadable directory is
    skipped."""
    lim_files, lim_depth, lim_entries = walk_limits()
    max_files = lim_files if max_files is None else max_files
    max_depth = lim_depth if max_depth is None else max_depth
    max_dir_entries = lim_entries if max_dir_entries is None else max_dir_entries
    entry_budget = max(max_files, 1) * _ENTRY_BUDGET_FACTOR + max_dir_entries
    res = WalkResult()
    root = Path(root).expanduser()
    if not root.is_dir():
        return res
    seen_dirs = set()
    entries_read = 0
    queue = deque([(root, "", 0)])
    while queue:
        d, rel_d, depth = queue.popleft()
        try:
            st = d.stat()
            key = (st.st_dev, st.st_ino)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            with os.scandir(d) as it:
                # Never read a huge directory whole: one entry past the cap is enough
                # to know it is over it.
                entries = list(islice(it, max_dir_entries + 1))
        except OSError:
            continue
        entries_read += len(entries)
        if len(entries) > max_dir_entries:
            res.partial_dirs.append(rel_d or ".")
            if entries_read > entry_budget:
                res.truncated = (f"the scan stopped after reading {entries_read} "
                                 "directory entries")
                return res
            continue
        entries.sort(key=lambda e: e.name)
        for e in entries:
            rel = f"{rel_d}/{e.name}" if rel_d else e.name
            try:
                is_link = e.is_symlink()
                if e.is_dir(follow_symlinks=False) or (is_link and e.is_dir()):
                    if _skip_dir(e.name, rel_d):
                        continue
                    # The one symlink followed: a top-level data/ that IS a link (the
                    # engine's output dir; the old top-level scan read through it).
                    if is_link and not (rel_d == "" and e.name == "data"):
                        if len(res.symlinked_dirs) >= _MAX_SYMLINKED:
                            res.truncated = res.truncated or (
                                f"more than {_MAX_SYMLINKED} symlinked directories")
                        else:
                            res.symlinked_dirs.append(rel)
                        continue
                    if depth + 1 > max_depth:
                        res.truncated = res.truncated or (
                            f"directories deeper than {max_depth} levels were not scanned")
                        continue
                    queue.append((Path(e.path), rel, depth + 1))
                    continue
                if is_link or not e.is_file(follow_symlinks=False):
                    continue
                if _skip_file(e.name, rel_d):
                    continue
                if len(res.files) >= max_files:
                    res.truncated = f"the scan stopped at the {max_files}-file cap"
                    return res
                s = e.stat(follow_symlinks=False)
                res.files[rel] = (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)
            except OSError:
                continue
        if entries_read > entry_budget:
            res.truncated = f"the scan stopped after reading {entries_read} directory entries"
            return res
    return res


def _iso(mtime_ns: int) -> str:
    return (datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def _under(rel: str, dirs: Iterable[str]) -> bool:
    for d in dirs or ():
        if d == "." or rel == d or rel.startswith(d.rstrip("/") + "/"):
            return True
    return False


def diff_snapshots(before: WalkResult, after: WalkResult, started_ns: int) -> List[dict]:
    """Files new or changed in `after` relative to `before`, sorted by path.

    A file absent from `before` where `before` could not see it (the walk was cut, or its
    directory was too large to list) may simply have been missed, so it is counted only
    when it changed after the attempt started (minus the slack)."""
    out = []
    for rel in sorted(after.files):
        cur = after.files[rel]
        size, mtime_ns = cur[0], cur[1]
        prior = before.files.get(rel)
        if prior is None:
            if ((before.truncated or _under(rel, before.partial_dirs))
                    and max(cur[1:3]) < started_ns - MTIME_SLACK_NS):
                continue
            change = "new"
        elif tuple(prior) != tuple(cur):
            change = "modified"
        else:
            continue
        out.append({"path": rel, "bytes": size, "modified": _iso(mtime_ns), "change": change})
    return out


# ── stdout path checks ───────────────────────────────────────────────────────

# Extensions a printed token must carry to count as a file path: data, figures, text and
# archives. Not `.py`: a library path in a printed warning is not an output. A dotted
# module name (`np.linalg`, `scipy.optimize`) or a number never matches.
_FILE_EXTS = frozenset("""
csv tsv txt dat json jsonl ndjson yaml yml toml xml html htm md rst tex log out
npy npz h5 hdf5 hdf he5 nc cdf fits fit fz parquet feather arrow pkl pickle joblib
mat sav zarr db sqlite asdf ecsv vot votable root
png jpg jpeg gif webp svg pdf eps ps tif tiff bmp
gz bz2 xz zip tar tgz
""".split())
# Extensions that are also common attribute or module names (`np.log`, `model.fit`,
# `self.out`, `np.mat`, `ROOT.root`, `astropy.io.fits`, `norm.cdf`, `ruamel.yaml`). A
# bare token with one of them (no `/`) is kept only when it resolves to a real file;
# it is never reported as absent.
_AMBIGUOUS_EXTS = frozenset("log fit fits out mat root db ps md sav dat cdf yaml".split())

# A path-like token: optional ~, ./ or ../ or /, segments of [\w.+-], an extension.
# Not preceded by a path character (`:` is allowed: `Saved to:/abs/answer.json`); not
# followed by a path character or `(` (a method call: `np.save(`, `model.fit(`). No
# spaces. A `host:port/...` tail is dropped by `_PORT_RE`.
_STDOUT_PATH_RE = re.compile(
    r"(?<![\w/.\-~+])"
    r"((?:~/|\.{1,2}/|/)?(?:[\w.+\-]+/)*[\w+\-][\w.+\-]*\.([A-Za-z][A-Za-z0-9]{0,7}))"
    r"(?![\w/\-(])"
)
_PORT_RE = re.compile(r"\d+/")
# A scheme-less URL: the first segment is a host name (`www.example.com/data.csv`).
_HOST_RE = re.compile(r"(?:www\.[^/]+|[\w\-.]+\.(?:com|org|net|edu|gov|io|ai|int|info"
                      r"|uk|de|fr|eu|ch|jp|cn|us|ca|au|nl|es|it))/", re.IGNORECASE)
# A save verb earlier on the same line marks the token as a claimed output, which is
# checked first when stdout names more paths than are checked.
_SAVE_CTX_RE = re.compile(
    r"\b(sav(?:e|ed|es|ing)|wr(?:ote|ite|ites|iting|itten)|output|export(?:ed|ing)?"
    r"|dump(?:ed|ing)?|stor(?:e|ed|ing)|creat(?:e|ed|ing)|produc(?:e|ed|ing))\b|->|=>",
    re.IGNORECASE,
)


def _stdout_candidates(stdout: str) -> List[Tuple[str, bool, bool]]:
    """`(token, ambiguous, after_save_verb)` per unique path-shaped token, in order of
    first appearance."""
    if not stdout:
        return []
    text = stdout
    if len(text) > _STDOUT_SCAN_CHARS:
        half = _STDOUT_SCAN_CHARS // 2
        text = text[:half] + "\n" + text[-half:]
    out: List[Tuple[str, bool, bool]] = []
    seen = set()
    for m in _STDOUT_PATH_RE.finditer(text):
        tok, ext = m.group(1).rstrip("."), m.group(2).lower()
        if ext not in _FILE_EXTS or len(tok) > 300 or "//" in tok or tok in seen:
            continue
        if m.start() and text[m.start() - 1] == ":" and _PORT_RE.match(tok):
            continue  # localhost:8000/index.html
        if _HOST_RE.match(tok):
            continue  # www.example.com/data.csv
        seen.add(tok)
        line_start = text.rfind("\n", 0, m.start()) + 1
        saved = bool(_SAVE_CTX_RE.search(text, line_start, m.start()))
        out.append((tok, ext in _AMBIGUOUS_EXTS and "/" not in tok, saved))
        if len(out) >= _MAX_CANDIDATES:
            break
    return out


def _select(cands: List[Tuple[str, bool, bool]], max_paths: int) -> List[Tuple[str, bool]]:
    """At most `max_paths` candidates: those after a save verb first, then the LAST
    others (a script that lists its inputs first and saves its answer last still has the
    answer checked). Returned in order of appearance."""
    if len(cands) <= max_paths:
        return [(t, a) for t, a, _s in cands]
    idx = [i for i, c in enumerate(cands) if c[2]][-max_paths:]
    rest = [i for i, c in enumerate(cands) if not c[2]]
    room = max_paths - len(idx)
    keep = sorted(idx + (rest[-room:] if room > 0 else []))
    return [(cands[i][0], cands[i][1]) for i in keep]


def stdout_path_tokens(stdout: str, max_paths: int = MAX_STDOUT_PATHS) -> List[str]:
    """The file-path-shaped tokens in stdout that are checked, in order of appearance,
    de-duplicated and capped (see `_select`). Only tokens with a known file extension
    count."""
    return [t for t, _a in _select(_stdout_candidates(stdout), max_paths)]


def _rel_to(path: Path, root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


class _Roots:
    """The work dir as given, resolved, and (when `data/` is a symlink) where data/
    really is, so a path printed through any alias maps to one relative path."""

    def __init__(self, work_dir):
        self.root = Path(work_dir).expanduser() if work_dir else None
        self.real = None
        self.data_real = None
        if self.root is None:
            return
        try:
            self.real = Path(os.path.realpath(self.root))
            data = self.root / "data"
            if data.is_symlink():
                self.data_real = Path(os.path.realpath(data))
        except OSError:
            self.real = self.root

    def rel(self, p: Path) -> Optional[str]:
        if self.root is None:
            return None
        r = _rel_to(p, self.root) or _rel_to(p, self.real)
        if r is not None:
            return r
        try:
            real = Path(os.path.realpath(p))
        except OSError:
            return None
        r = _rel_to(real, self.real)
        if r is None and self.data_real is not None:
            d = _rel_to(real, self.data_real)
            if d is not None:
                r = "data" if d == "." else f"data/{d}"
        return r


def _suffix_match(tok: str, pools: Sequence[Iterable[str]]) -> Tuple[Optional[str], int]:
    """`(rel, pool index)` of the first file in `pools` whose path ends with `tok`."""
    parts = [s for s in Path(tok).parts if s not in (".", "")]
    if not parts or ".." in parts:
        return None, -1
    suffix = "/".join(parts)
    for i, pool in enumerate(pools):
        for rel in pool:
            if rel == suffix or rel.endswith("/" + suffix):
                return rel, i
    return None, -1


def check_stdout_paths(stdout: str, run_cwd, work_dir, after: Optional[WalkResult],
                       written: Iterable[str], started_ns: int,
                       max_paths: int = MAX_STDOUT_PATHS) -> List[dict]:
    """Classify each path stdout names as `written` / `pre_existing` / `missing` /
    `not_found`.

    `{"path": <as printed>, "resolved": <absolute>, "status", "bytes"?, "in_work_dir",
    "rel"?, "matched_by"?}`. Relative tokens resolve against the script's cwd; one that
    is not there is matched by path suffix against the files this attempt wrote, then
    against the rest of the walk (`matched_by: "suffix"`). A file the walk covered is
    judged by the snapshot diff; anything else (outside the work dir, inside an excluded
    or partial directory, past a cap) by whether it changed after the attempt started.
    A bare name found nowhere is `not_found` (it may have been saved elsewhere), never
    `missing`; an ambiguous bare name (`np.log`) found nowhere is dropped. Only `stat()`
    is called; nothing is opened, no directory is listed, and a directory named in
    stdout is not classified here (`expand_stdout_groups` counts it from the walk).
    `build` then turns a `pre_existing` file an earlier attempt of this step wrote into
    `carried_over`."""
    written = set(written)
    cwd = Path(run_cwd).expanduser() if run_cwd else None
    roots = _Roots(work_dir)
    walked = after.files if after is not None else {}
    written_rel = sorted(w for w in written if not w.startswith("/"))
    out: List[dict] = []
    for tok, ambiguous in _select(_stdout_candidates(stdout), max_paths):
        p = Path(tok).expanduser()
        relative = not p.is_absolute()
        if relative:
            if cwd is None:
                continue
            p = cwd / p
        try:
            p = Path(os.path.normpath(p))
            rel = roots.rel(p)
            try:
                st = p.stat()
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                st = None
        except OSError:
            continue
        entry = {"path": tok, "resolved": str(p)}
        if st is None and relative and roots.root is not None:
            match, pool = _suffix_match(tok, (written_rel, sorted(walked)))
            if match is not None:
                rel = match
                entry["resolved"] = str(roots.root / match)
                entry["matched_by"] = "suffix"
                entry["status"] = "written" if pool == 0 else "pre_existing"
                if match in walked:
                    entry["bytes"] = walked[match][0]
                    entry["mtime_ns"] = walked[match][1]
                    entry["change_ns"] = max(walked[match][1:3])
                else:
                    try:
                        s2 = (roots.root / match).stat()
                        entry["bytes"], entry["mtime_ns"] = s2.st_size, s2.st_mtime_ns
                        entry["change_ns"] = changed_ns(s2)
                    except OSError:
                        entry["bytes"], entry["mtime_ns"] = 0, started_ns
        if "status" not in entry:
            if st is None:
                if ambiguous:
                    continue
                entry["status"] = "missing" if ("/" in tok) else "not_found"
            elif _stat.S_ISDIR(st.st_mode):
                continue
            else:
                entry["bytes"] = st.st_size
                if rel is not None and rel in walked:
                    entry["status"] = "written" if rel in written else "pre_existing"
                else:
                    entry["status"] = ("written"
                                       if changed_ns(st) >= started_ns - MTIME_SLACK_NS
                                       else "pre_existing")
                entry["mtime_ns"] = st.st_mtime_ns
                entry["change_ns"] = changed_ns(st)
        entry["in_work_dir"] = rel is not None
        if rel is not None:
            entry["rel"] = rel
        out.append(entry)
    return out


# ── the step's history: carried over vs pre-existing ─────────────────────────
#
# Kept in `logs/step_N_history.json` (an engine directory, so the walk never sees it),
# not in graph state: the baseline can hold `max_files` entries, and every traced node
# span records the whole input state. State keeps only `step_history`, a small reference
# (`{"path", "run", "key"}`; `{"inline": ..., "run"}` when there is no work dir, where
# the baseline is empty). The file outlives the graph invoke, so a step restarted after
# a kill (deep_research resumes at `step_index`) still knows which files its own killed
# run wrote: a fresh invoke that finds the file with the same step key (step number plus
# a hash of the sub-task and its bullets, so a re-plan that reuses the number starts
# clean) keeps the first run's baseline and reads the old run's writes as "a previous
# run of this step". `run` counts those invokes.

HISTORY_VERSION = 1


def step_key(step) -> str:
    """Identifies a step's task across graph invokes: a hash of its sub-task and
    bullet points. Never raises."""
    import hashlib

    try:
        text = str(getattr(step, "sub_task", "") or "") + "\n" + "\n".join(
            str(b) for b in (getattr(step, "bullet_points", None) or []))
    except Exception:  # noqa: BLE001
        text = repr(step)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def history_path(logs_dir, step_number) -> Optional[Path]:
    return None if logs_dir is None else Path(logs_dir) / f"step_{step_number}_history.json"


def freeze_baseline(before: Optional[WalkResult], started_ns: int) -> dict:
    """The step's baseline: the snapshot taken right before the step's FIRST executed
    attempt (bounded by the walk's caps), plus what that walk could not see. Taken once
    per step and never overwritten (a restarted run keeps the first run's)."""
    if before is None:
        return {"files": {}, "truncated": "no run directory was set", "partial_dirs": [],
                "symlinked_dirs": [], "started_ns": started_ns}
    return {"files": {rel: list(sig) for rel, sig in before.files.items()},
            "truncated": before.truncated, "partial_dirs": list(before.partial_dirs),
            "symlinked_dirs": list(before.symlinked_dirs), "started_ns": started_ns}


def _file_sig(p) -> Optional[List[int]]:
    try:
        s = os.stat(p, follow_symlinks=False)
    except (OSError, ValueError):
        return None
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino]


def _tag_order(a) -> Tuple[int, int]:
    """Sort key for an attempt tag: this run's attempts, then a previous run's, then
    unrecorded."""
    if isinstance(a, int) and not isinstance(a, bool):
        return (0, a)
    if isinstance(a, str) and a.startswith("prev:"):
        try:
            return (1, int(a[5:]))
        except ValueError:
            return (1, 0)
    if isinstance(a, str) and a.isdigit():
        return (0, int(a))
    return (2, 0)


class StepHistory:
    """What the step knew before this attempt ran: its baseline and which attempt last
    wrote each file (`writes`: path -> [run, attempt, signature or None]). `prior(path,
    ...)` tells a file this attempt did NOT write apart: `(True, k)` written by attempt k
    of this run of the step and unchanged since, `(True, "prev:k")` written by attempt k
    of a previous run of the step (before a restart) and unchanged since, `(True, None)`
    changed after the step began but by no recorded attempt (or changed after the
    recorded one wrote it), `(False, None)` on disk before the step began."""

    __slots__ = ("baseline", "writes", "attempt", "run", "path", "key", "step_number")

    def __init__(self, baseline: Optional[dict] = None, writes: Optional[dict] = None,
                 attempt: int = 0, run: int = 1, path: Optional[Path] = None,
                 key: Optional[str] = None, step_number=None):
        self.baseline = baseline if isinstance(baseline, dict) else None
        self.writes: Dict[str, list] = {}
        for k, v in (writes or {}).items():
            if isinstance(v, int) and not isinstance(v, bool):
                self.writes[k] = [int(run), v, None]
            elif isinstance(v, (list, tuple)) and len(v) >= 2:
                sig = v[2] if len(v) > 2 and isinstance(v[2], (list, tuple)) else None
                self.writes[k] = [int(v[0]), int(v[1]), list(sig) if sig else None]
        self.attempt = int(attempt or 0)
        self.run = int(run or 1)
        self.path = Path(path) if path else None
        self.key = key
        self.step_number = step_number

    # ── persistence ──

    @classmethod
    def load(cls, ref, logs_dir, step_number, key: str, attempt: int) -> "StepHistory":
        """The step's history for this attempt. `ref` is `state["step_history"]` (None at
        the first executor pass of a graph invoke). Never raises: an unreadable file
        starts a fresh history, which reads every file this attempt did not write as
        pre-existing (the behaviour without history)."""
        path = history_path(logs_dir, step_number)
        if isinstance(ref, dict) and isinstance(ref.get("inline"), dict):
            data = ref["inline"]
            return cls(data.get("baseline"), data.get("writes"), attempt,
                       int(ref.get("run") or 1), None, key, step_number)
        data = None
        if path is not None:
            try:
                import json

                raw = json.loads(path.read_text())
                if (isinstance(raw, dict) and raw.get("version") == HISTORY_VERSION
                        and raw.get("step_key") == key):
                    data = raw
            except (OSError, ValueError):
                data = None
        if isinstance(ref, dict) and ref.get("run"):
            run = int(ref["run"])  # this invoke's own file, from an earlier attempt
            if data is not None and int(data.get("runs") or 0) != run:
                data = None  # not the file this invoke wrote
        else:
            # A fresh invoke: a matching file is a previous run of this step.
            run = int(data.get("runs") or 0) + 1 if data is not None else 1
        if data is None:
            return cls(None, None, attempt, run, path, key, step_number)
        return cls(data.get("baseline"), data.get("writes"), attempt, run, path, key,
                   step_number)

    def save(self) -> dict:
        """Write the history file (atomically) and return the reference for graph state;
        with no work dir, the (small) history itself rides in the reference."""
        data = {"version": HISTORY_VERSION, "step_number": self.step_number,
                "step_key": self.key, "runs": self.run, "baseline": self.baseline,
                "writes": self.writes}
        if self.path is None:
            return {"inline": data, "run": self.run}
        import json

        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.path)
        return {"path": str(self.path), "run": self.run, "key": self.key}

    def ensure_baseline(self, before: Optional[WalkResult], started_ns: int) -> bool:
        """Take the baseline if the step has none yet. True when it was taken now."""
        if self.baseline is not None:
            return False
        self.baseline = freeze_baseline(before, started_ns)
        return True

    def record(self, manifest: Iterable[dict], after: Optional[WalkResult], root,
               cap: Optional[int] = None) -> None:
        """After an attempt: each manifest path now belongs to this attempt (with its
        signature, so a later change by something else is noticed); paths no longer on
        disk are dropped. Bounded by `cap` (the walk's file cap): past it the oldest
        attempts' entries go first, and such a file then reads as "written earlier in
        this step" without a number (the baseline still tells it apart from a
        pre-existing one)."""
        walked = after.files if after is not None else {}
        root_p = Path(root).expanduser() if root else None

        def where(k):
            if k.startswith("/") or root_p is None:
                return Path(k)
            return root_p / k

        for entry in manifest or ():
            path = entry.get("path") if isinstance(entry, dict) else None
            if not path:
                continue
            sig = list(walked[path]) if path in walked else _file_sig(where(path))
            self.writes.pop(path, None)
            self.writes[path] = [self.run, self.attempt, sig]
        for k in list(self.writes):
            if k in walked:
                continue
            try:
                gone = not os.path.lexists(where(k))
            except (OSError, ValueError):
                gone = False
            if gone:
                del self.writes[k]
        cap = walk_limits()[0] if cap is None else cap
        if len(self.writes) > cap:
            keep = sorted(self.writes.items(), key=lambda kv: (kv[1][0], kv[1][1]))[-cap:]
            self.writes = dict(keep)

    # ── classification ──

    def tag(self, run: int, attempt: int):
        return attempt if run == self.run else f"prev:{attempt}"

    def prior(self, key: str, sig=None, change_ns: Optional[int] = None,
              cur_sig=None) -> Tuple[bool, object]:
        """`sig` is the file's walk signature when the walk saw it (None otherwise);
        `cur_sig` its current signature from a `stat` when the walk did not."""
        cur = sig if sig is not None else cur_sig
        rec = self.writes.get(key)
        if rec is not None:
            rsig = rec[2] if len(rec) > 2 else None
            if rsig is not None and cur is not None and tuple(rsig) != tuple(cur):
                return True, None  # changed after that attempt wrote it, by no attempt
            return True, self.tag(rec[0], rec[1])
        b = self.baseline
        if b is None:
            return False, None
        files = b.get("files") or {}
        if sig is not None and key in files:
            return tuple(files[key]) != tuple(sig), None
        seen = (sig is not None and not key.startswith("/") and not b.get("truncated")
                and not _under(key, b.get("partial_dirs") or ())
                and not _under(key, b.get("symlinked_dirs") or ()))
        if seen:
            return True, None  # the baseline walk saw its directory and it was not there
        started = b.get("started_ns")
        if change_ns is not None and started is not None and \
                change_ns >= started - MTIME_SLACK_NS:
            return True, None
        return False, None


def _carried_summary(carried: List[dict], stdout_named: set) -> dict:
    """The carried-over files as the prompt shows them: all of them up to
    `MAX_CARRIED_LISTED`, otherwise the stdout-named and root ones (at most
    `_MAX_CARRIED_KEPT`) and one line per directory and attempt for the rest."""
    total = sum(int(e.get("bytes") or 0) for e in carried)
    if len(carried) <= MAX_CARRIED_LISTED:
        return {"count": len(carried), "bytes": total, "listed": list(carried),
                "groups": [], "more": None}
    order = sorted(range(len(carried)),
                   key=lambda i: (_rank(carried[i], stdout_named, set()), i))
    keep = {i for i in order[:_MAX_CARRIED_KEPT]
            if _rank(carried[i], stdout_named, set()) <= 2}
    listed = [e for i, e in enumerate(carried) if i in keep]
    groups: Dict[Tuple[str, object], List[int]] = {}
    for i, e in enumerate(carried):
        if i in keep:
            continue
        path = str(e.get("path", ""))
        parent = path.rsplit("/", 1)[0] + "/" if "/" in path else "./"
        g = groups.setdefault((parent, e.get("attempt")), [0, 0])
        g[0] += 1
        g[1] += int(e.get("bytes") or 0)
    ranked = sorted(groups.items(),
                    key=lambda kv: (-kv[1][0], kv[0][0], _tag_order(kv[0][1])))
    shown = [{"dir": d, "attempt": a, "files": n, "bytes": b}
             for (d, a), (n, b) in ranked[:_MAX_RENDERED_GROUPS]]
    extra = ranked[_MAX_RENDERED_GROUPS:]
    more = ({"files": sum(v[0] for _k, v in extra), "groups": len(extra)}
            if extra else None)
    return {"count": len(carried), "bytes": total, "listed": listed, "groups": shown,
            "more": more}


# Globs and directories named in stdout. A token is cut at whitespace and stripped of
# quoting and sentence punctuation; a glob needs a `*` and either a file extension or a
# `/`-separated `*` segment (so `**bold**` and `2*np.pi/3` are not globs); a directory
# needs a `/` and must be an existing directory inside the run directory (`stat` only).
_TOKEN_STRIP_LEAD = "([{<'\"`"
_TOKEN_STRIP_TRAIL = ")]}>'\"`,;:!?."
_GLOB_SHAPE = re.compile(r"(?:~/|\.{1,2}/|/)?[\w.+\-*]+(?:/[\w.+\-*]+)*/?")
_DIR_SHAPE = re.compile(r"(?:~/|\.{1,2}/|/)?[\w.+\-]+(?:/[\w.+\-]+)*/?")


def _is_glob(tok: str) -> bool:
    if "*" not in tok or "//" in tok or not _GLOB_SHAPE.fullmatch(tok):
        return False
    # Something literal to anchor it: `*/`, `**/` and `*.*` (printed C comments,
    # markdown) would otherwise count every file of a directory.
    if not re.search(r"[A-Za-z0-9_]", tok):
        return False
    last = tok.rstrip("/").rsplit("/", 1)[-1]
    if last in ("*", "**"):
        return "/" in tok
    m = re.search(r"\.([A-Za-z][A-Za-z0-9]{0,7}|\*)$", last)
    return bool(m) and (m.group(1) == "*" or m.group(1).lower() in _FILE_EXTS)


def _group_candidates(stdout: str) -> List[Tuple[str, str, bool]]:
    """`(token, kind, after_save_verb)` per unique glob- or directory-shaped token."""
    if not stdout:
        return []
    text = stdout
    if len(text) > _STDOUT_SCAN_CHARS:
        half = _STDOUT_SCAN_CHARS // 2
        text = text[:half] + "\n" + text[-half:]
    out: List[Tuple[str, str, bool]] = []
    seen = set()
    for line in text.splitlines():
        for m in re.finditer(r"\S+", line):
            tok = m.group(0).lstrip(_TOKEN_STRIP_LEAD).rstrip(_TOKEN_STRIP_TRAIL)
            if "=" in tok:  # out=results/ -> results/
                tok = tok.rsplit("=", 1)[-1].lstrip(_TOKEN_STRIP_LEAD)
            if not tok or len(tok) > 300 or tok in seen:
                continue
            if _is_glob(tok):
                kind = "glob"
            elif ("/" in tok and "//" not in tok and _DIR_SHAPE.fullmatch(tok)
                  and not _HOST_RE.match(tok)
                  and Path(tok.rstrip("/")).suffix.lower().lstrip(".") not in _FILE_EXTS):
                kind = "directory"
            else:
                continue
            seen.add(tok)
            saved = bool(_SAVE_CTX_RE.search(line, 0, m.start()))
            out.append((tok, kind, saved))
            if len(out) >= _MAX_CANDIDATES:
                return out
    return out


def _glob_regex(pattern: str) -> "re.Pattern":
    """A glob over relative posix paths: `*` within one segment, `**` across them."""
    parts = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(parts))


def _unscanned_note(rel_dir: str, after: WalkResult) -> bool:
    """Could the walk have missed files under `rel_dir`?"""
    top = rel_dir.split("/", 1)[0]
    if top in ENGINE_DIRS or any(seg.startswith(".") or seg in CACHE_DIRS
                                 for seg in rel_dir.split("/") if seg):
        return True
    return bool(after.truncated) or _under(rel_dir, after.partial_dirs) or _under(
        rel_dir, after.symlinked_dirs) or any(_under(d, [rel_dir]) for d in
                                               after.partial_dirs + after.symlinked_dirs)


def expand_stdout_groups(stdout: str, run_cwd, work_dir, after: Optional[WalkResult],
                         written: Iterable[str], carried: Optional[Dict[str, object]],
                         max_groups: int = MAX_STDOUT_GROUPS) -> List[dict]:
    """Count the files a stdout glob or directory covers, by class, from the walk alone
    (nothing is listed or opened): `{"path", "kind", "rel", "total", "written",
    "carried_over": {attempt: n}, "pre_existing", "unscanned", "classified",
    "matched_by"?, "matched_dirs"?}`. `carried_over` keys are attempt tags as strings
    ("3", "prev:3" for a previous run of the step, "?" for an unrecorded attempt).
    `carried` None means the step has no history: the files this attempt did not write
    are then counted in `pre_existing` with `classified: False` ("not written by this
    attempt", nothing more). Only globs and directories inside the run directory (not
    the run directory itself) are counted; others are dropped, since listing outside it
    is not this check's business. A glob that matches nothing is kept only when its
    directory exists and it is anchored (a directory in the pattern, or a save verb
    before it), so `W/m*2.csv` or `a*b.dat` in prose is not reported."""
    if after is None or not work_dir:
        return []
    roots = _Roots(work_dir)
    cwd = Path(run_cwd).expanduser() if run_cwd else roots.root
    written = set(written)
    cands = _group_candidates(stdout)
    # Tokens after a save verb first, then the latest others; a token that is not a
    # glob or a directory in the run directory (`and/or`, `km/s`) does not use a slot,
    # but at most `_GROUP_TRIES` tokens are resolved (one or two `stat`s each).
    order = ([i for i, c in enumerate(cands) if c[2]][::-1]
             + [i for i, c in enumerate(cands) if not c[2]][::-1])
    found: List[Tuple[int, dict]] = []
    for i in order[:_GROUP_TRIES]:
        if len(found) >= max_groups:
            break
        tok, kind, saved = cands[i]
        entry = _count_group(tok, kind, saved, cwd, roots, after, written, carried)
        if entry is not None:
            found.append((i, entry))
    return [e for _i, e in sorted(found, key=lambda t: t[0])]


_MAX_MATCHED_DIRS = 3


def _count_group(tok: str, kind: str, saved: bool, cwd: Path, roots: "_Roots",
                 after: WalkResult, written: set, carried: Optional[Dict[str, object]]
                 ) -> Optional[dict]:
    """One glob or directory token counted by class, or None when it is not one inside
    the run directory."""
    raw = Path(tok.rstrip("/") or "/").expanduser()
    relative = not raw.is_absolute()
    extra: dict = {}
    if kind == "glob":
        segs = tok.rstrip("/").split("/")
        cut = next(i for i, sg in enumerate(segs) if "*" in sg)
        head = "/".join(segs[:cut])
        if tok.startswith("/") and not head:
            head = "/"
        head_path = Path(head).expanduser() if head else Path(".")
        if not head_path.is_absolute():
            head_path = cwd / head_path
        head_path = Path(os.path.normpath(head_path))
        rel_head = roots.rel(head_path)
        if rel_head is None:
            return None
        tail = "/".join(segs[cut:])
        rel_pat = tail if rel_head in ("", ".") else f"{rel_head}/{tail}"
        rx = _glob_regex(rel_pat)
        matches = [r for r in after.files if rx.fullmatch(r)]
        head_parts = [sg for sg in head.split("/") if sg not in ("", ".")]
        try:
            head_exists = head_path.is_dir()
        except OSError:
            head_exists = False
        if not matches and not head_exists and relative and head_parts \
                and ".." not in head_parts:
            # Its directory is not at the script's cwd: printed after os.chdir, or
            # relative to a subdirectory. Match by suffix, and say where it matched.
            rx2 = re.compile("(?:.*/)?" + _glob_regex("/".join(head_parts + [tail])).pattern)
            matches = [r for r in after.files if rx2.fullmatch(r)]
            if matches:
                dirs = sorted({r.rsplit("/", 1)[0] + "/" if "/" in r else "./"
                               for r in matches})
                extra = {"matched_by": "suffix", "matched_dirs": dirs[:_MAX_MATCHED_DIRS],
                         "matched_dir_count": len(dirs)}
        if not matches and not (head_exists and (head_parts or saved)):
            return None
        scope_dir = rel_head if rel_head not in ("", ".") else ""
    else:
        p = raw if not relative else cwd / raw
        try:
            p = Path(os.path.normpath(p))
            st = p.stat()
        except OSError:
            return None
        if not _stat.S_ISDIR(st.st_mode):
            return None
        rel_dir = roots.rel(p)
        if rel_dir in (None, "", "."):
            return None
        rel_pat = rel_dir
        prefix = rel_dir.rstrip("/") + "/"
        matches = [r for r in after.files if r.startswith(prefix)]
        scope_dir = rel_dir
    counts = {"written": 0, "pre_existing": 0}
    by_attempt: Dict[str, int] = {}
    known = carried if carried is not None else {}
    for r in matches:
        if r in written:
            counts["written"] += 1
        elif r in known:
            a = known[r]
            key = "?" if a is None else str(a)
            by_attempt[key] = by_attempt.get(key, 0) + 1
        else:
            counts["pre_existing"] += 1
    out = {"path": tok, "kind": kind, "rel": rel_pat, "total": len(matches),
           "written": counts["written"], "carried_over": by_attempt,
           "pre_existing": counts["pre_existing"], "classified": carried is not None,
           "unscanned": _unscanned_note(scope_dir, after) if scope_dir
           else bool(after.truncated)}
    out.update(extra)
    return out


# ── building the attempt's manifest ─────────────────────────────────────────


def build(before: WalkResult, after: WalkResult, started_ns: int, stdout: str,
          run_cwd, work_dir, history: Optional[StepHistory] = None
          ) -> Tuple[List[dict], dict]:
    """`(data_manifest, data_manifest_info)` for a local attempt (see module docstring).
    The manifest is the whole diff; nothing is cut for display here. `history` (the
    step's baseline and earlier attempts' writes) splits the files this attempt did not
    write into carried over and pre-existing; without it every such file is
    pre-existing, as before."""
    files = diff_snapshots(before, after, started_ns)
    written = {f["path"] for f in files}
    checks = check_stdout_paths(stdout, run_cwd, work_dir, after, written, started_ns)
    # A stdout-named file this attempt wrote that the walk could not see (outside the
    # work dir, an excluded, partial or symlinked directory, past a cap) joins the list.
    for c in checks:
        if c["status"] != "written":
            continue
        key = c.get("rel") or c["resolved"]
        if key in written:
            continue
        written.add(key)
        files.append({"path": key, "bytes": c.get("bytes", 0),
                      "modified": _iso(c["mtime_ns"]), "change": "written",
                      "source": "stdout"})
    carried, groups, failed = _classify_unwritten_safely(
        checks, after, written, history, stdout, run_cwd, work_dir)
    for c in checks:
        c.pop("mtime_ns", None)
        c.pop("change_ns", None)
    reason = before.truncated or after.truncated
    info = {
        "scope": "work_dir",
        "complete": reason is None,
        "incomplete_reason": reason,
        "omitted": 0,
        "symlinked_dirs": sorted(set(before.symlinked_dirs) | set(after.symlinked_dirs)),
        "partial_dirs": sorted(set(before.partial_dirs) | set(after.partial_dirs)),
        "stdout_paths": checks + groups,
        "started_ns": started_ns,
        "attempt": history.attempt if history is not None else None,
        "step_run": history.run if history is not None else None,
        "history": history is not None and not failed,
        "carried_over": carried,
    }
    if failed:
        info["history_error"] = failed
    return files, info


_EMPTY_CARRIED = {"count": 0, "bytes": 0, "listed": [], "groups": [], "more": None}


def _classify_unwritten_safely(checks, after, written, history, stdout, run_cwd, work_dir
                               ) -> Tuple[dict, List[dict], Optional[str]]:
    """`_classify_unwritten`, but a failure in it costs only the carried-over split and
    the glob counts: the diff and the per-file stdout checks survive (an exception in
    `build` would empty the whole manifest)."""
    saved = [dict(c) for c in checks]
    try:
        carried, groups = _classify_unwritten(checks, after, written, history, stdout,
                                              run_cwd, work_dir)
        return carried, groups, None
    except Exception as e:  # noqa: BLE001 — bookkeeping must never cost the manifest
        checks[:] = saved
        return dict(_EMPTY_CARRIED), [], type(e).__name__


def _classify_unwritten(checks: List[dict], after: Optional[WalkResult], written: set,
                        history: Optional[StepHistory], stdout: str, run_cwd, work_dir
                        ) -> Tuple[dict, List[dict]]:
    """Mark each stdout-checked file an earlier attempt of this step wrote as
    `carried_over` (in place), and return `(carried-over summary, glob/directory
    counts)`."""
    walked = after.files if after is not None else {}
    carried_list: List[dict] = []
    carried_map: Dict[str, object] = {}
    if history is not None:
        for rel in sorted(walked):
            if rel in written:
                continue
            cur = walked[rel]
            is_c, att = history.prior(rel, cur, max(cur[1:3]))
            if is_c:
                carried_map[rel] = att
                carried_list.append({"path": rel, "bytes": cur[0], "attempt": att})
        for c in checks:
            if c["status"] != "pre_existing":
                continue
            rel = c.get("rel")
            if rel is not None and rel in walked:
                if rel in carried_map:
                    c["status"], c["attempt"] = "carried_over", carried_map[rel]
                continue
            key = rel or c["resolved"]
            is_c, att = history.prior(key, None, c.get("change_ns"),
                                      cur_sig=_file_sig(c["resolved"]))
            if is_c:
                c["status"], c["attempt"] = "carried_over", att
                if key not in carried_map:
                    carried_map[key] = att
                    carried_list.append({"path": key, "bytes": c.get("bytes", 0),
                                         "attempt": att, "source": "stdout"})
    stdout_named = {c.get("rel") or c.get("resolved") for c in checks} - {None}
    groups = expand_stdout_groups(stdout, run_cwd, work_dir, after, written,
                                  carried_map if history is not None else None)
    return _carried_summary(carried_list, stdout_named), groups


def no_work_dir_info(stdout: str, run_cwd, started_ns: int,
                     history: Optional[StepHistory] = None) -> Tuple[List[dict], dict]:
    """A run with no work dir (ephemeral tempfile): nothing to walk, only stdout checks."""
    checks = check_stdout_paths(stdout, run_cwd, None, None, (), started_ns)
    files = [{"path": c["resolved"], "bytes": c.get("bytes", 0),
              "modified": _iso(c["mtime_ns"]), "change": "written", "source": "stdout"}
             for c in checks if c["status"] == "written"]
    carried, _groups, failed = _classify_unwritten_safely(
        checks, None, {f["path"] for f in files}, history, "", run_cwd, None)
    for c in checks:
        c.pop("mtime_ns", None)
        c.pop("change_ns", None)
    return files, {"scope": "none", "complete": False,
                   "incomplete_reason": "no run directory was set, so it was not scanned",
                   "omitted": 0, "symlinked_dirs": [], "partial_dirs": [],
                   "stdout_paths": checks, "started_ns": started_ns,
                   "attempt": history.attempt if history is not None else None,
                   "step_run": history.run if history is not None else None,
                   "history": history is not None and not failed,
                   "carried_over": carried}


def remote_info() -> dict:
    """The guest reports the top level of its own data/; nothing is checked engine-side."""
    return {"scope": "remote_data", "complete": True, "incomplete_reason": None,
            "omitted": 0, "symlinked_dirs": [], "partial_dirs": [], "stdout_paths": []}


def attempt_start_ns(logs_dir: Optional[Path]) -> int:
    """The reference time for "changed since the attempt started": the earlier of the
    engine clock and the filesystem timestamp of a sentinel touched in `logs/` (then
    removed), so a networked filesystem whose clock is skewed from the engine's is
    compared on its own clock. Never raises."""
    now = time.time_ns()
    if logs_dir is None:
        return now
    sentinel = Path(logs_dir) / ".attempt_start"
    try:
        sentinel.write_text("")
        fs_ns = sentinel.stat().st_mtime_ns
    except OSError:
        return now
    finally:
        try:
            sentinel.unlink()
        except OSError:
            pass
    return min(now, fs_ns)


# ── readers shared by the consumers ─────────────────────────────────────────


def written_names(manifest) -> set:
    """Every path and basename in a manifest, for presence tests by path or name."""
    names = set()
    for entry in manifest or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path:
            continue
        names.add(str(path))
        names.add(str(path).rsplit("/", 1)[-1])
    return names


def listing_is_whole(info: Optional[dict]) -> bool:
    """False when the walk hit a cap, or (an older state) entries were cut from the
    manifest. A missing info (old checkpoint, a test building state by hand) counts as
    whole, which is what the old top-level listing assumed."""
    if not isinstance(info, dict):
        return True
    return bool(info.get("complete", True)) and not info.get("omitted")


def written_since_start(path: Path, info: Optional[dict]) -> bool:
    """Is `path` a regular file changed (mtime or ctime) since the attempt started? Used
    only where the snapshot could not see the path."""
    started = (info or {}).get("started_ns") if isinstance(info, dict) else None
    if started is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return _stat.S_ISREG(st.st_mode) and changed_ns(st) >= started - MTIME_SLACK_NS


def under_unscanned_dir(rel: str, info: Optional[dict]) -> bool:
    """Is `rel` under a directory the walk did not list file by file (symlinked, or
    with more entries than the per-directory cap)?"""
    if not isinstance(info, dict):
        return False
    return _under(rel, info.get("symlinked_dirs") or ()) or _under(
        rel, info.get("partial_dirs") or ())


# Kept for callers of the earlier name.
under_symlinked_dir = under_unscanned_dir


_EXCLUDED_NOTE = ("engine directories " + ", ".join(f"{d}/" for d in sorted(ENGINE_DIRS))
                  + " and hidden/cache directories excluded")

_STDOUT_STATUS = {
    "written": "exists; written or modified by this attempt",
    "pre_existing": "exists, but was NOT written or modified by this attempt or an "
                    "earlier attempt of this step (it was on disk before this step "
                    "began: an earlier step's output or a provided input)",
    "missing": "does NOT exist",
    "not_found": "no file at this name in the script's working directory, and no file "
                 "of this name in the scanned run directory (a bare name; it may refer "
                 "to a file saved elsewhere)",
}
# Without the step's history (an info dict built without it) a file this attempt did not
# write can only be said to be that.
_PRE_EXISTING_NO_HISTORY = ("exists, but was NOT written or modified by this attempt "
                            "(it was already on disk before the run)")

CARRIED_OVER_HEADER = ("----- FILES CARRIED OVER FROM EARLIER ATTEMPTS OF THIS STEP "
                       "(unchanged by this attempt) --")


def _from_attempt(attempt, current, restarted: bool = False) -> str:
    """"attempt 5", or what to say when the number is unknown, is this attempt's own (an
    escalation re-runs the same attempt), or belongs to a previous run of the step (it
    was restarted after a kill, or run again)."""
    if attempt is None or attempt == "?":
        if restarted:
            return ("an earlier attempt of this step, in this run or in a run of it "
                    "before it was restarted (attempt not recorded)")
        return "an earlier attempt of this step (attempt not recorded)"
    if isinstance(attempt, str) and attempt.startswith("prev:"):
        return (f"attempt {attempt[5:]} of an earlier run of this same step (before the "
                "step was restarted)")
    try:
        if current is not None and int(attempt) == int(current):
            return f"an earlier run of this attempt ({attempt}, before it was re-run)"
    except (TypeError, ValueError):
        pass
    return f"attempt {attempt}"


def _restarted(info) -> bool:
    try:
        return int((info or {}).get("step_run") or 1) > 1
    except (TypeError, ValueError):
        return False


def _carried_status(c: dict, current, restarted: bool = False) -> str:
    return (f"exists; written by {_from_attempt(c.get('attempt'), current, restarted)} and "
            "NOT rewritten by this attempt (carried over from earlier in this step)")


def _group_line(c: dict, current, restarted: bool = False) -> str:
    what = "pattern" if c.get("kind") == "glob" else "directory"
    where = ""
    if c.get("matched_by") == "suffix" and c.get("matched_dirs"):
        dirs = ", ".join(c["matched_dirs"])
        more = c.get("matched_dir_count", 0) - len(c["matched_dirs"])
        where = (f" (no such directory at the script's working directory; matched by "
                 f"name under {dirs}" + (f" and {more} more" if more > 0 else "") + ")")
    elif (c.get("kind") == "directory" and c.get("rel")
          and c["rel"].rstrip("/") != str(c["path"]).rstrip("/")):
        where = f" (as {c['rel']}/ in the run directory)"
    note = (" The scan did not cover all of it, so the counts may be low."
            if c.get("unscanned") else "")
    if not c.get("total"):
        return (f"- {c['path']} ({what}{where}): no file in the scanned run directory "
                f"matches.{note}")
    parts = [f"{c.get('written', 0)} written by this attempt"]
    if c.get("classified", True):
        for att, n in sorted((c.get("carried_over") or {}).items(),
                             key=lambda kv: _tag_order(kv[0])):
            parts.append(f"{n} carried over from {_from_attempt(att, current, restarted)} "
                         "(unchanged by this attempt)")
        parts.append(f"{c.get('pre_existing', 0)} on disk before this step began")
    else:
        parts.append(f"{c.get('pre_existing', 0)} not written by this attempt")
    return (f"- {c['path']} ({what}{where}): {c['total']} file(s): "
            + "; ".join(parts) + "." + note)


def render_carried_over(info: Optional[dict], input_dir_empty: bool = False) -> str:
    """The FILES CARRIED OVER section, or "" when there are none (or no history): files
    an earlier attempt of THIS step wrote and this attempt left unchanged, by attempt,
    summarised per directory when long. Read from `data_manifest_info["carried_over"]`,
    which the executor already shortened. `input_dir_empty` (the data-safety fact, when
    it is shown) adds that these files were generated within the step, not provided."""
    if not isinstance(info, dict):
        return ""
    co = info.get("carried_over")
    if not isinstance(co, dict) or not co.get("count"):
        return ""
    current = info.get("attempt")
    restarted = _restarted(info)
    lines = [CARRIED_OVER_HEADER,
             f"{co['count']} file(s), {_human(int(co.get('bytes') or 0))}, written in the "
             "run directory by an earlier attempt of this same step and left unchanged by "
             "this one (paths relative to the run directory). They exist, but this "
             "attempt did not write them:"]
    for e in co.get("listed") or []:
        where = ", named in stdout" if e.get("source") == "stdout" else ""
        lines.append(f"- {e['path']} ({e.get('bytes', '?')} bytes{where}; from "
                     f"{_from_attempt(e.get('attempt'), current, restarted)})")
    for g in co.get("groups") or []:
        lines.append(f"- {g['dir']}: {g['files']} file(s) from "
                     f"{_from_attempt(g.get('attempt'), current, restarted)}, "
                     f"{_human(int(g.get('bytes') or 0))}")
    more = co.get("more")
    if more:
        lines.append(f"- ... and {more['files']} more file(s) in {more['groups']} other "
                     "director(ies)/attempt(s)")
    if input_dir_empty:
        lines.append("data/ was EMPTY before this step's first attempt ran, so every file "
                     "above was generated within this step by its own code, not provided "
                     "as input: one that stands in for a provided input dataset is "
                     "self-created, and the data-integrity rules apply to it.")
    lines.append("-" * 45)
    return "\n".join(lines)


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
                         ".eps", ".tif", ".tiff", ".bmp"})


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _rank(entry: dict, stdout_named: set, priority: set) -> int:
    path = str(entry.get("path", ""))
    if path in priority or path.rsplit("/", 1)[-1] in priority:
        return 0
    if entry.get("source") == "stdout" or path in stdout_named:
        return 1
    if "/" not in path:
        return 2
    if Path(path).suffix.lower() in _IMAGE_EXTS:
        return 3
    return 4


def _choose(manifest: List[dict], info: Optional[dict], priority: Iterable[str],
            limit: int) -> Tuple[List[dict], List[dict]]:
    """`(shown, rest)`: declared outputs, stdout-named files, root files and figures
    first, then the others in path order; `shown` keeps the manifest's order."""
    if len(manifest) <= limit:
        return list(manifest), []
    stdout_named = {c.get("rel") for c in ((info or {}).get("stdout_paths") or [])
                    if isinstance(info, dict) and not c.get("kind")} - {None}
    prio = set()
    for p in priority or ():
        prio.add(p)
        prio.add(p.rsplit("/", 1)[-1])
    order = sorted(range(len(manifest)),
                   key=lambda i: (_rank(manifest[i], stdout_named, prio), i))
    keep = set(order[:limit])
    return ([e for i, e in enumerate(manifest) if i in keep],
            [e for i, e in enumerate(manifest) if i not in keep])


def _summarise(rest: List[dict]) -> List[str]:
    groups: Dict[str, List[int]] = {}
    for e in rest:
        path = str(e.get("path", ""))
        parent = path.rsplit("/", 1)[0] + "/" if "/" in path else "./"
        g = groups.setdefault(parent, [0, 0])
        g[0] += 1
        g[1] += int(e.get("bytes") or 0)
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0]))
    lines = [f"- {d}: {n} more file(s) written by this attempt, {_human(b)}"
             for d, (n, b) in ranked[:_MAX_RENDERED_GROUPS]]
    if len(ranked) > _MAX_RENDERED_GROUPS:
        extra = ranked[_MAX_RENDERED_GROUPS:]
        lines.append(f"- ... and {sum(v[0] for _d, v in extra)} more file(s) in "
                     f"{len(extra)} other director(ies)")
    return lines


def render_for_evaluator(manifest: List[dict], info: Optional[dict],
                         priority: Iterable[str] = ()) -> str:
    """The step evaluator's OUTPUT FILES section: exact about what was checked.
    `priority` (the step's declared outputs) is listed first when the manifest is longer
    than `MAX_RENDERED`; the rest is summarised per directory."""
    manifest = manifest or []
    has_info = isinstance(info, dict)
    scope = info.get("scope") if has_info else None
    partial = (info.get("partial_dirs") or []) if has_info else []
    whole = has_info and bool(info.get("complete", True)) and not partial
    lines: List[str] = []
    if scope == "remote_data":
        if manifest:
            lines.append("Files this attempt wrote or modified in data/ on the execution "
                         "sandbox (top level of data/ only; files elsewhere are not "
                         "visible to the engine):")
        else:
            lines.append("No file in data/ on the execution sandbox was written or modified "
                         "by this attempt (top level of data/ only; files elsewhere are not "
                         "visible to the engine).")
    elif scope == "none":
        lines.append("No run directory was set, so written files were not tracked; only "
                     "the file paths named in stdout were checked.")
    elif scope == "work_dir":
        if manifest:
            lines.append("Files in the run directory written or modified by this attempt "
                         f"(checked recursively; paths relative to the run directory; "
                         f"{_EXCLUDED_NOTE}):")
        elif whole:
            lines.append("No file in the run directory was written or modified by this "
                         f"attempt (checked recursively; {_EXCLUDED_NOTE}).")
        else:
            lines.append("No written file was found in the scanned part of the run "
                         f"directory (checked recursively; {_EXCLUDED_NOTE}).")
    else:  # no info: a state built before this field existed
        if not manifest:
            lines.append("No file was recorded as written or modified by this attempt.")
    shown, rest = _choose(manifest, info, priority, MAX_RENDERED)
    for f in shown:
        where = ""
        if f.get("source") == "stdout":
            where = (", outside the run directory; named in stdout"
                     if str(f.get("path", "")).startswith("/") else ", named in stdout")
        lines.append(f"- {f['path']} ({f.get('bytes', '?')} bytes{where})")
    lines.extend(_summarise(rest))
    if has_info:
        if info.get("omitted"):
            lines.append(f"- ... and {info['omitted']} more file(s) written by this attempt, "
                         "not listed")
        if scope == "work_dir" and not info.get("complete", True):
            lines.append(f"NOTE: this listing is INCOMPLETE ({info.get('incomplete_reason')}). "
                         "A file this attempt wrote may be missing from it, so do not "
                         "conclude from its absence alone that a file was not written.")
        if partial:
            names = ", ".join("the run directory itself" if d == "." else f"{d}/"
                              for d in partial[:20])
            more = f" and {len(partial) - 20} more" if len(partial) > 20 else ""
            lines.append("Directories too large to list file by file (files this attempt "
                         f"wrote in them are not listed): {names}{more}.")
        sym = info.get("symlinked_dirs") or []
        if sym:
            more = f" and {len(sym) - 20} more" if len(sym) > 20 else ""
            lines.append("Symlinked directories were not scanned: "
                         + ", ".join(f"{d}/" for d in sym[:20]) + more + ".")
        checks = info.get("stdout_paths") or []
        if checks:
            lines.append("")
            lines.append("File paths named in stdout, checked on disk (stat only, "
                         "contents not read; a pattern or directory is counted against "
                         "the scanned run directory, by class):")
            current = info.get("attempt")
            restarted = _restarted(info)
            for c in checks:
                if c.get("kind"):
                    lines.append(_group_line(c, current, restarted))
                    continue
                if c["status"] == "carried_over":
                    status = _carried_status(c, current, restarted)
                elif c["status"] == "pre_existing" and not info.get("history"):
                    status = _PRE_EXISTING_NO_HISTORY
                else:
                    status = _STDOUT_STATUS.get(c["status"], c["status"])
                if c.get("matched_by") == "suffix" and c.get("rel"):
                    status += f" (found as {c['rel']})"
                elif c["status"] == "missing" and not str(c["path"]).startswith(("/", "~")):
                    status += " (resolved against the script's working directory)"
                lines.append(f"- {c['path']}: {status}")
        elif scope in ("work_dir", "none"):
            lines.append("stdout names no file path to check.")
    return "\n".join(lines)


def image_paths(work_dir, manifest) -> List[Path]:
    """Absolute paths of the manifest's entries, in order (relative ones resolve against
    the work dir; a stdout-named absolute path stays as it is)."""
    wd = Path(work_dir).expanduser() if work_dir else None
    out = []
    for entry in manifest or []:
        rel = (entry or {}).get("path", "") if isinstance(entry, dict) else str(entry or "")
        if not rel:
            continue
        p = Path(rel)
        if not p.is_absolute():
            if wd is None:
                continue
            p = wd / p
        out.append(p)
    return out


def other_workspace_files(work_dir, skip_top: Iterable[str] = ("data",),
                          max_files: Optional[int] = None) -> Tuple[List[Tuple[str, int]], Optional[str]]:
    """Files in the work dir beyond the top level of `skip_top` dirs and outside the
    engine directories, for the workspace listings: `([(rel, bytes)], truncated)`, where
    `truncated` also names the directories too large to list."""
    snap = snapshot_tree(work_dir, max_files=max_files)
    skip = set(skip_top)
    rows = []
    for rel, meta in sorted(snap.files.items()):
        parts = rel.split("/")
        if len(parts) == 2 and parts[0] in skip:
            continue
        rows.append((rel, meta[0]))
    note = snap.truncated
    if snap.partial_dirs:
        big = ", ".join(f"{d}/" for d in snap.partial_dirs[:10])
        note = "; ".join(x for x in (note, f"too large to list: {big}") if x)
    return rows, note


__all__ = [
    "ENGINE_DIRS", "CACHE_DIRS", "MAX_RENDERED", "MTIME_SLACK_NS",
    "WalkResult", "snapshot_tree", "diff_snapshots", "stdout_path_tokens",
    "check_stdout_paths", "build", "no_work_dir_info", "remote_info", "attempt_start_ns",
    "changed_ns", "written_names", "listing_is_whole", "written_since_start",
    "under_unscanned_dir", "under_symlinked_dir", "render_for_evaluator", "image_paths",
    "other_workspace_files", "walk_limits", "freeze_baseline", "step_key",
    "history_path", "HISTORY_VERSION", "StepHistory", "expand_stdout_groups", "render_carried_over", "CARRIED_OVER_HEADER",
    "MAX_CARRIED_LISTED", "MAX_STDOUT_GROUPS",
]
