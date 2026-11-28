"""Declared-output post-condition — the deterministic half of the goal gate.

Runs between `executor` and `execution_evaluator`, with `ast`-free text parsing and no
model call, and asks one question of the finished run: the sub-task named an output
artifact by PATH — "save the cleaned cohort to `data/cohort.parquet`" — is that file
actually there?

A clean exit with the file missing is otherwise discovered only by `step_evaluator`,
i.e. after the execution verdict AND the step verdict have both been paid for, and the
engineer then gets "the required file was not produced" from a model instead of from the
filesystem. The same bounce costs nothing here, and names the exact path.

Shaped like `static_preflight`'s refusal — the executor's result is rewritten into a
failure (`execution_returncode=1`, the diagnosis appended to stderr) — but it does NOT
hand the verdict to `execution_evaluator`. Whether a declared file is on disk is a
FACT, so the node writes the `ExecutionVerdict` itself, appends the `error_history`
entry and demotes `step_{N}.py`/`.log` exactly as the code gate would, and
`route_after_output_postcondition` sends the attempt straight back to the engineer
(or to END at the attempt cap, so the bounce stays bounded by `max_n_attempts`).
Asking a model to confirm a `stat()` call would cost one round-trip per bounce for
nothing, and would make a deterministic bounce depend on the model agreeing.

The router is deliberately its own one-liner rather than a reuse of
`route_after_execution_evaluator`: the data-safety layers on that path adjudicate
MISSING INPUT (`safe_loading`, the intent gate, `data_fixer`), and "the script did not
save its own output" is none of those. Keeping them apart means a missing output can
never be mistaken for a missing input and terminate the step.

stdout is left untouched: the script really did print what it printed, and the engineer
needs to see it.

PRESENCE IS JUDGED FROM THE PER-ATTEMPT MANIFEST, NOT FROM `Path.exists()`. The
executor re-snapshots the run directory (recursively, see `attempt_manifest`) at the
start of every attempt and diffs it afterwards, so `data_manifest` is exactly "written
or modified by THIS attempt", at any depth; a bare existence check
would be waved through by a file an EARLIER step or an earlier attempt left on disk.
That is not hypothetical — it is the measured case: metabric Project3/Iteration1 step 5
declared `data/loocv_results.parquet`, a stale copy from a previous run was sitting in
`data/`, and the eight attempts that never wrote it would all have passed an
existence check. It is also consistent with how the engine treats attempts: each one is
a complete standalone script (see `executor`), so a step writes its own declared outputs
whatever earlier steps or earlier runs left on disk.

A DECLARED OUTPUT AN EARLIER ATTEMPT OF THIS STEP WROTE COUNTS IN ONE CASE ONLY: that
attempt belongs to the same graph invoke of the step, its script exited 0 without timing
out, and the file's signature (size, mtime, ctime, inode) is unchanged since
(`attempt_manifest.StepHistory.reusable`, read from `logs/step_N_history.json`, not from
the shortened `carried_over` summary). This is what a resumable script does on a retry:
the sub-task says "if the file already exists and validates, load it and skip
recomputation", attempt 2 saved it and was rejected by the step evaluator for another
reason, attempt 3 loads it. Demanding a rewrite there contradicts the sub-task and, in the
measured case below, was half of a loop that used every attempt. What still does NOT
count, because each reopens the stale-file hole above:
  * a file that was on disk before the step began (an earlier step's output, a provided
    input, a leftover of an earlier run of the whole task) — it has no write record;
  * a file written by an attempt whose script did not exit 0 (it may have failed after
    writing it), or by an execution before the `clean` field existed;
  * a file changed by anything since the attempt that wrote it;
  * a file a PREVIOUS run of the step wrote (a restart after a kill, or a re-run of the
    same plan in the same directory: the history cannot tell these two apart, and the
    second is the stale-earlier-run case).
Whether the earlier attempt's content is the right answer stays the step evaluator's
judgement; it is shown the same file as carried over, with the attempt number.

WHAT THE STEP'S TEXT SAYS ABOUT A PATH (`read_step_paths`). Each sentence of the sub-task
line and of each bullet is read on its own, and a `data/...` path in it is one of:
  declared   a save-type verb governs it, affirmatively and unconditionally;
  protected  a prohibition governs it and names a write-type verb ("do not overwrite or
             delete `data/a.json`", "without modifying `data/a.json`", "leave
             `data/a.json` untouched", "`data/a.json` must not be changed");
  unsure     the sentence has a negation or a condition this parser cannot attribute
             ("if recomputation is needed, save `data/b.json`", "do not overwrite
             `data/a.json` unless it is invalid", a negation followed by a second verb
             phrase joined by a comma or "and"); the path is then NEITHER declared nor
             protected, and the node says so once per step in its log;
  nothing    an input, a path with no verb, or a prohibition on reading only.
Across the texts of one step, an unsure reading of a path, or one text declaring it and
another protecting it, makes it unsure. The measured case: a bullet "Atomically save
`data/b.json` with the same structure ... Do not overwrite or delete `data/a.json` ..."
was read by the earlier parser (nearest preceding save verb in the whole bullet) as
declaring BOTH files, so the attempt that correctly wrote only `data/b.json` was bounced
for not writing `data/a.json`, the next attempt obeyed and rewrote it, and the step
evaluator rejected that one for overwriting a protected file; six attempts alternated and
the step failed. Sentence by sentence, the first path is declared and the second
protected.

A PROTECTED FILE THE ATTEMPT CHANGED IS BOUNCED TOO, with the path and the protecting
sentence named: the manifest says `change == "modified"` (the file existed when the
attempt started and its signature differs) or `data_manifest_info["deleted"]` lists it.
Creating a protected path that did not exist is left to the step evaluator.

A DISAGREEMENT WITH THE STEP EVALUATOR IS SETTLED IN THE EVALUATOR'S FAVOUR
(`arbitrate_with_step_evaluator`, called by `step_evaluator` on a rejection). The
evaluator reads the sentence; this module pattern-matches it. When a rejected attempt
wrote a path this module had declared and the verdict objects to that write (an unmet
requirement that protects the path, or feedback saying the run overwrote it against the
sub-task), the path is dropped from the declared set for the rest of the step
(`output_postcondition_waived`); symmetrically a protected path the verdict wants written
loses its protection. If the module had already bounced an attempt over that path, the
engineer's next prompt carries both messages and says which one stands
(`output_conflict_note`). With the sentence reading above the measured case never gets
here; this is the net for wording the patterns do not parse.

CONSERVATIVE in five independent ways, because a false positive burns an attempt on
correct code:
  1. only SAVE-shaped declarations count. For each candidate path the nearest preceding
     verb decides: `load the cohort from data/raw.csv` is an INPUT (an absent input is
     the `safe_loading` layer's business — bouncing it here would pressure the engineer
     into inventing one), `write it to data/clean.csv` is an output; a path under a
     prohibition or a condition is not a declaration (see above);
  2. only unambiguous `data/...` paths with a real extension — a placeholder
     (`data/{run}.csv`, `data/*.png`) never parses;
  3. only on a run that otherwise SUCCEEDED (exit 0, no timeout). After a crash the
     missing file is already explained by the traceback;
  4. never under `CMBAGENT_LG_REMOTE_EXECUTOR`: the data lives guest-side, so engine-side
     absence proves nothing; and never without a manifest to read;
  5. a declared path the manifest could not see — the walk hit a cap
     (`data_manifest_info["complete"]` is False) or the path is under a symlinked
     directory the walk does not enter or one too large to list file by file
     (`partial_dirs`) — counts as written when it changed (mtime or ctime) after the
     attempt started. Never a bare existence check: a stale copy is older than the
     attempt, so the metabric case above is still caught. The manifest is never cut for
     display (only the evaluator's rendering is), so a step that also writes hundreds of
     other files is still judged on the full list. A declared path in a
     subdirectory of `data/` (`data/plots/km.png`) is judged from the manifest like any
     other, since the snapshot is recursive.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from langgraph.runtime import Runtime

from langgraph.graph import END

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import attempt_manifest as _am
from cmbagent_lg.self_debug.nodes import _codebase_dir, _logs_dir
from cmbagent_lg.self_debug.schemas import ExecutionVerdict
from cmbagent_lg.self_debug.state import DebugState
from cmbagent_lg.timing import timed_node


# Prefix the rejection carries, mirroring `static_preflight.STATIC_MARKER`.
POSTCONDITION_MARKER = "[post-run check"

# A path under `data/` with a plain extension. The character class admits no brace,
# angle bracket, star or question mark, so a templated or globbed path simply does not
# match. `(?<![\w/.-])` stops a longer path (`raw_data/x.csv`) matching at `data/`.
_PATH_RE = re.compile(r"(?<![\w/.\-])(?:\./)?(data/[\w][\w./\-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/\-])")

# The verb immediately before a path decides whether it is an output or an input.
_SAVE_RE = re.compile(
    r"\b(sav(?:e|es|ed|ing)|writ(?:e|es|ing)|written|stor(?:e|es|ed|ing)"
    r"|export(?:s|ed|ing)?|output(?:s|ted|ting)?|dump(?:s|ed|ing)?"
    r"|persist(?:s|ed|ing)?|produc(?:e|es|ed|ing)|generat(?:e|es|ed|ing)"
    r"|creat(?:e|es|ed|ing)|to_csv|to_parquet|to_pickle|savefig|np\.save)\b",
    re.IGNORECASE,
)
_LOAD_RE = re.compile(
    r"\b(load(?:s|ed|ing)?|read(?:s|ing)?|open(?:s|ed|ing)?|import(?:s|ed|ing)?"
    r"|pars(?:e|es|ed|ing)|ingest(?:s|ed|ing)?|input(?:s)?|provided|given"
    r"|available|existing|supplied|fetch(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)


# ── reading the step's text ──────────────────────────────────────────────────
#
# Everything below is pattern matching on one sentence at a time. It never has to be
# right about a sentence it cannot parse: such a path becomes "unsure" and is neither
# declared nor protected (the step evaluator still judges it).

# Verbs that change or remove a file. Under a prohibition they make the path protected.
_MODIFY_RE = re.compile(
    r"\b(overwrit(?:e|es|ing|ten)|overwrote|delet(?:e|es|ed|ing|ion|ions)"
    r"|remov(?:e|es|ed|ing|al|als)|modif(?:y|ies|ied|ying|ication|ications)"
    r"|touch(?:es|ed|ing)?|re-?sav(?:e|es|ed|ing)|re-?writ(?:e|es|ing|ten)|rewrote"
    r"|chang(?:e|es|ed|ing)|edit(?:s|ed|ing)?|alter(?:s|ed|ing|ation|ations)?"
    r"|replac(?:e|es|ed|ing|ement)|updat(?:e|es|ed|ing)|truncat(?:e|es|ed|ing)"
    r"|clobber(?:s|ed|ing)?|renam(?:e|es|ed|ing)|mov(?:e|es|ed|ing)|append(?:s|ed|ing)?"
    r"|regenerat(?:e|es|ed|ing)|re-?creat(?:e|es|ed|ing)|recomput(?:e|es|ed|ing)"
    r"|eras(?:e|es|ed|ing)|unlink(?:s|ed|ing)?|wip(?:e|es|ed|ing))\b",
    re.IGNORECASE,
)
_MODIFY_NOUNS = (r"changes?|modifications?|edits?|overwrit(?:e|es|ing)|deletions?|removals?"
                 r"|writes?|writing|updates?|alterations?|re-?writ(?:e|es|ing)"
                 r"|re-?sav(?:e|es|ing)")

# A prohibition. The `nominal` ones can also take a noun ("without the index"), so they
# are read as governing a path only through a verb list (`_negated_verbs`). A double
# negative ("do not forget to save") and "not only ... but also" are not prohibitions.
_NEG_RE = re.compile(
    r"\b(?:"
    r"(?P<nominal>without|avoid(?:s|ed|ing)?|refrain(?:s|ed|ing)?\s+from|instead\s+of"
    r"|rather\s+than|no(?=\s+(?:[\w-]+\s+)?(?:" + _MODIFY_NOUNS + r")\b))"
    r"|do(?:es)?\s+not|do(?:es)?n[’']t|never|must\s+not|mustn[’']t|should\s+not"
    r"|shouldn[’']t|shall\s+not|can\s*not|can[’']t|may\s+not|will\s+not|won[’']t"
    r"|need\s+not|needn[’']t|not\s+to"
    r")(?![\w’'])"
    r"(?!\s+(?:only|just|merely|simply|solely)\b|\s+(?:forget|fail|omit|neglect|skip|hesitat)\w*)",
    re.IGNORECASE,
)
# Words after which a negation earlier in the sentence no longer applies.
_RESET_RE = re.compile(
    r"\b(?:but|instead(?!\s+of)|rather(?!\s+than)|however|then|yet|whereas|while"
    r"|although|though)\b", re.IGNORECASE)

# The words allowed between the last negated verb and the path: determiners,
# prepositions and generic nouns for a file. Anything else ("the header twice into")
# means the path is not the verb's object, and the reading is unsure.
_DET = (r"the|a|an|any|this|that|these|those|its|their|of|to|at|in|into|from|on|onto"
        r"|under|over|inside|within|named|called|file|files|path|paths|artifact|artifacts"
        r"|artefact|artefacts|checkpoint|checkpoints|existing|earlier|previous|prior"
        r"|original|current|final|already|other|either|both|all|content|contents")
_OBJ_TAIL_RE = re.compile(r"(?:\s*\b(?:" + _DET + r")\b)*[\s,:`'\"(\[*\-–•]*$", re.IGNORECASE)
_ADVERB = (r"(?:(?:otherwise|ever|even|also|again|further"
           r"|(?!only\b|merely\b|simply\b|solely\b)[A-Za-z]+ly)\s+)*")
_ITEM_RE = re.compile(
    _ADVERB + r"([A-Za-z][\w'’-]*)(?:\s+(?:on|to|over|into|from|upon|up|out))?"
    r"(?:\s+(?:in\s+any\s+way|at\s+all|again))?", re.IGNORECASE)
_CONJ_RE = re.compile(r"\b(or|nor|and)\b", re.IGNORECASE)
# What may stand between two paths of one list: "`a`, `b`, or any other file".
_LIST_SEP_RE = re.compile(
    r"[`'\"*)\]\s]*"
    r"(?:(?:,|/|&|\+|-|–|•|\band\b|\bor\b|\bnor\b|\bplus\b|\bas\s+well\s+as\b)\s*)+"
    r"(?:(?:the|any|other|all|both|either|also|file|files)\s+)*"
    r"[`'\"*(\[\s]*", re.IGNORECASE)
_OR_TAIL_RE = re.compile(r"[\s`'\"),*\]]*(?:or|nor)\s+", re.IGNORECASE)

# Protection stated AFTER the path: "leave `a` untouched", "`a` must not be modified".
_POST_STATE_RE = re.compile(
    r"[\s`'\"),(*\]]*(?:and\s+(?:leave|keep)\s+(?:it|them|both)\s+)?(?:as\s+)?"
    r"(?:(?:must|should|shall|will|has\s+to|have\s+to|needs?\s+to|is\s+to|are\s+to)\s+)?"
    r"(?:(?:remains?|stays?|be|is|are)\s+)?(?:(?:left|kept)\s+)?(?:[A-Za-z]+ly\s+)?"
    r"(?:untouched|unchanged|unmodified|unaltered|intact|as[- ]is|read[- ]only"
    r"|byte[- ]identical)\b", re.IGNORECASE)
_POST_PASSIVE_RE = re.compile(
    r"[\s`'\"),(*\]]*(?:must|should|shall|may|can|will|is|are)\s*(?:\s+not|\s+never|n[’']t)"
    r"\s+(?:to\s+)?(?:ever\s+)?be\s+(?:[A-Za-z]+ly\s+)?"
    r"(?:over-?written|re-?written|written|modified|changed|deleted|removed|touched|edited"
    r"|altered|replaced|re-?saved|saved|updated|regenerated|re-?created|recomputed"
    r"|truncated|renamed|moved|clobbered)\b", re.IGNORECASE)

# What may follow a path a prohibition governs for the path to be protected: the end of
# the sentence, punctuation, a further "or ..." / "and ...", or a phrase that only says
# where or when ("in this step", "at all") or gives a reason ("because ..."). Any other
# continuation qualifies the prohibition ("do not write `a` in text mode" forbids a
# manner, not the file) and the reading is unsure.
_PLAIN_END_RE = re.compile(
    r"[\s`'\"*)\]]*(?:$|[.,;:!(—–]|-\s|(?:or|nor|and|because|since|which)\b"
    r"|(?:in|during|within|throughout)\s+(?:this|the|any)\s+(?:step|run|script|attempt)\b"
    r"|at\s+all\b|again\b|under\s+any\b|in\s+any\s+(?:way|case)\b|either\b|whatsoever\b)",
    re.IGNORECASE)

# Conditions. "even if" and "whether or not" state the opposite of a condition, and
# "or otherwise touch" / "anything else" are not conditions at all.
_NOT_COND_RE = re.compile(
    r"\beven\s+if\b|\bwhether\s+or\s+not\b|\bregardless\s+of\s+whether\b|\bas\s+if\b"
    r"|\b(?:or|and|nor)\s+otherwise\b|\b(?:any|some|no|every)(?:thing|one|where)\s+else\b",
    re.IGNORECASE)
_COND_WORDS = r"needed|necessary|required|applicable|appropriate|missing|invalid|absent|stale"
_COND_PRE_RE = re.compile(
    r"\b(?:if|unless|otherwise|else|in\s+case|optional(?:ly)?|provided\s+that"
    r"|as\s+long\s+as|only\s+when|(?:when|where|as)\s+(?:" + _COND_WORDS + r"))\b",
    re.IGNORECASE)
_COND_POST_RE = re.compile(
    r"[\s`'\"),*\]]*(?:but\s+)?(?:only\s+)?(?:if|unless|in\s+case|except|until"
    r"|(?:when|where|as)\s+(?:" + _COND_WORDS + r"))\b", re.IGNORECASE)
_COND_ANY_RE = re.compile(
    r"\b(?:if|unless|otherwise|else|in\s+case|except|until|provided\s+that"
    r"|as\s+long\s+as|only\s+when|(?:when|where|as)\s+(?:" + _COND_WORDS + r"))\b",
    re.IGNORECASE)
_MODAL_RE = re.compile(
    r"\b(?:may|can|could|might)\s+(?:also\s+|optionally\s+|then\s+)?$", re.IGNORECASE)

# Sentence boundaries: end punctuation followed by whitespace, a blank line, or a line
# that starts a list item. An abbreviation's full stop is not one.
_SENT_END_RE = re.compile(
    r"(?P<stop>[.!?;])[\"'`)\]*]*\s+|\n\s*\n+|\n\s*(?:[-*•–]|\d+[.)])\s+")
_ABBREV_RE = re.compile(r"\b(?:e\.g|i\.e|vs|cf|approx|resp|incl|fig|eq|no|ca)\.$",
                        re.IGNORECASE)
_EMPHASIS_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")

_WHY_NEGATION = "a negation precedes it in a sentence shape this check does not parse"
_WHY_SAVE_CONDITION = "the sentence saves it only under a condition"
_WHY_PROTECT_CONDITION = "the sentence protects it only under a condition"
_WHY_BOTH_IN_SENTENCE = "the sentence both saves it and says to leave it unchanged"
_WHY_BOTH_IN_STEP = "one sentence of the sub-task saves it and another protects it"
_WHY_QUALIFIED = "the words after it qualify the prohibition"


class StepPaths(NamedTuple):
    """What a step's text says about the `data/...` paths it names."""

    declared: List[str]            # must be written by the step
    protected: List[str]           # must not be modified or deleted by the step
    unsure: Dict[str, str]         # path -> why it is treated as neither
    protected_by: Dict[str, str]   # protected path -> the sentence that protects it


def _mask(text: str, pattern: "re.Pattern") -> str:
    return pattern.sub(lambda m: " " * len(m.group()), text)


def _sentences(text: str) -> List[str]:
    """The sentences of one sub-task line or bullet, whitespace collapsed. A list that a
    colon introduces ("Save the following:" and its items) stays one sentence."""
    text = _EMPHASIS_RE.sub(r"\1", text)
    out: List[str] = []
    start, in_list = 0, False
    for m in _SENT_END_RE.finditer(text):
        if m.group("stop"):
            if _ABBREV_RE.search(text[start:m.start() + 1]):
                continue
            in_list = False
        elif "\n\n" in m.group().replace(" ", "").replace("\t", ""):
            in_list = False
        elif in_list or text[start:m.start()].rstrip().endswith(":"):
            in_list = True
            continue
        out.append(text[start:m.end()])
        start = m.end()
    out.append(text[start:])
    return [" ".join(t.split()) for t in out if t.strip()]


def _affirmative(scope: str) -> Tuple[str, Optional[int]]:
    """The nearest-preceding-verb rule on `scope` (the text before the path):
    `("declared", where the save verb starts)`, `("input", None)` or `("none", None)`."""
    last_save = None
    for m in _SAVE_RE.finditer(scope):
        last_save = m
    if last_save is None:
        return "none", None  # no save verb at all → not a declared output
    last_load = max((m.end() for m in _LOAD_RE.finditer(scope)), default=None)
    if last_load is not None and last_load > last_save.end():
        return "input", None  # the nearer verb reads it → an input, not an output
    return "declared", last_save.start()


def _negated_verbs(seg: str) -> Optional[List[str]]:
    """`seg` is the text between a negation and a path. When it is a list of verbs
    followed by nothing but determiners ("overwrite or delete", "read, validate, write,
    re-save, or otherwise touch the existing"), return the verbs; otherwise None.

    A list of two or more needs a conjunction (a bare comma may start a new, affirmative
    clause: "do not wait, save `a`"), and a list joined only by "and" needs every item to
    be a file verb ("do not modify and re-save `a`" is one prohibition; "do not wait and
    save `a`" is not)."""
    head = _OBJ_TAIL_RE.sub("", seg, count=1).strip()
    if not head:
        return None
    head = re.sub(r"\band\s*/\s*or\b", "or", head, flags=re.IGNORECASE)
    conj = {"or"} if "/" in head else set()
    items: List[str] = []
    for chunk in re.split(r"\s*[,/]\s*", head):
        if not chunk.strip():
            return None
        for piece in _CONJ_RE.split(chunk):
            piece = piece.strip()
            if piece.lower() in ("or", "nor", "and"):
                conj.add(piece.lower())
            elif piece:
                m = _ITEM_RE.fullmatch(piece)
                if m is None:
                    return None
                items.append(m.group(1))
    if not items or (len(items) > 1 and not conj):
        return None
    if conj == {"and"} and not all(_is_file_verb(w) for w in items):
        return None
    return items


def _is_write_verb(word: str) -> bool:
    return bool(_SAVE_RE.search(word) or _MODIFY_RE.search(word))


def _is_file_verb(word: str) -> bool:
    return bool(_is_write_verb(word) or _LOAD_RE.search(word))


def _pre_reading(pre: str, prev_end: Optional[int]) -> Tuple[str, Optional[str], str]:
    """`(status, why, scope)` from the text before a path (or a list of paths).
    `prev_end` is where the previous path of the sentence ends. `scope` is the part of
    `pre` the reading rests on, which is where a condition is then looked for."""
    neg = None
    for m in _NEG_RE.finditer(pre):
        neg = m
    reset_end = max((m.end() for m in _RESET_RE.finditer(pre)), default=0)
    if neg is None:
        return _affirmative(pre)[0], None, pre
    if neg.start() < reset_end:
        # "do not overwrite `a`, but save the result to `b`": the negation is closed.
        scope = pre[reset_end:]
        return _affirmative(scope)[0], None, scope
    seg = pre[neg.end():]
    after_path = prev_end is not None and prev_end > neg.end()
    if after_path:
        # "do not overwrite `a` or delete `b`": only "or"/"nor" carries the negation on.
        seg = pre[prev_end:]
        m = _OR_TAIL_RE.match(seg)
        verbs = _negated_verbs(seg[m.end():]) if m else None
    else:
        verbs = _negated_verbs(seg)
    if verbs is not None:
        return ("protected" if any(_is_write_verb(v) for v in verbs) else "none"), None, pre
    if neg.group("nominal"):
        # "without masking, save it to `a`": a comma or "and" ends what "without" governs,
        # unless what follows is a second gerund ("without re-running and saving `a`").
        cut = max(seg.rfind(","), max((m.end() for m in re.finditer(r"\band\b", seg, re.I)),
                                      default=-1))
        if cut >= 0:
            scope = seg[cut:]
            first = _SAVE_RE.search(scope)
            if first is None or not first.group().lower().endswith("ing"):
                return _affirmative(scope)[0], None, scope
    status = _affirmative(pre)[0]
    if _SAVE_RE.search(seg) or _MODIFY_RE.search(seg):
        return "unsure", _WHY_NEGATION, pre
    if not neg.group("nominal") and status == "declared":
        return "unsure", _WHY_NEGATION, pre
    return status, None, pre


def _read_sentence(sent: str) -> List[Tuple[str, str, Optional[str]]]:
    """`(path, status, why)` for every path in one sentence; status is "declared",
    "protected", "unsure", "input" or "none"."""
    groups: List[list] = []
    for m in _PATH_RE.finditer(sent):
        if groups and _LIST_SEP_RE.fullmatch(sent, groups[-1][-1].end(), m.start()):
            groups[-1].append(m)   # "`a`, `b`, or `c`": one list, one reading
        else:
            groups.append([m])
    out: List[Tuple[str, str, Optional[str]]] = []
    prev_end: Optional[int] = None
    for i, group in enumerate(groups):
        first, last = group[0].start(), group[-1].end()
        nxt = groups[i + 1][0].start() if i + 1 < len(groups) else len(sent)
        pre, post = sent[:first], sent[last:nxt]
        status, why, scope = _pre_reading(pre, prev_end)
        if status == "protected" and not _PLAIN_END_RE.match(sent, last):
            status, why = "unsure", _WHY_QUALIFIED
        if _POST_STATE_RE.match(post) or _POST_PASSIVE_RE.match(post):
            if status == "declared":
                status, why = "unsure", _WHY_BOTH_IN_SENTENCE
            elif status != "unsure":
                status = "protected"
        if status == "declared":
            scope_m = _mask(scope, _NOT_COND_RE)
            verb_at = _affirmative(scope)[1]
            if (_COND_PRE_RE.search(scope_m)
                    or _COND_POST_RE.match(_mask(post, _NOT_COND_RE))
                    or (verb_at is not None and _MODAL_RE.search(scope[:verb_at]))):
                status, why = "unsure", _WHY_SAVE_CONDITION
        elif status == "protected" and _COND_ANY_RE.search(_mask(sent, _NOT_COND_RE)):
            status, why = "unsure", _WHY_PROTECT_CONDITION
        out += [(m.group(1), status, why) for m in group]
        prev_end = last
    return out


def _read_texts(texts) -> StepPaths:
    seen: Dict[str, set] = {}
    why: Dict[str, str] = {}
    protected_by: Dict[str, str] = {}
    for text in texts:
        if not text or not isinstance(text, str):
            continue
        for sent in _sentences(text):
            for path, status, reason in _read_sentence(sent):
                if status in ("none", "input"):
                    continue
                seen.setdefault(path, set()).add(status)
                if status == "unsure" and reason:
                    why.setdefault(path, reason)
                if status == "protected":
                    protected_by.setdefault(path, sent)
    declared: List[str] = []
    protected: List[str] = []
    unsure: Dict[str, str] = {}
    for path, statuses in seen.items():   # insertion order: first mention first
        if "unsure" in statuses:
            unsure[path] = why.get(path, _WHY_NEGATION)
        elif {"declared", "protected"} <= statuses:
            unsure[path] = _WHY_BOTH_IN_STEP
        elif "declared" in statuses:
            declared.append(path)
        else:
            protected.append(path)
    return StepPaths(declared, protected, unsure,
                     {p: protected_by[p] for p in protected})


def read_step_paths(step) -> StepPaths:
    """Declared, protected and unsure `data/...` paths of a step (see the module
    docstring). The sub-task line and each bullet are read sentence by sentence; a verb
    or a negation in one sentence says nothing about a path in another."""
    texts = [getattr(step, "sub_task", "") or ""]
    texts += [b or "" for b in (getattr(step, "bullet_points", None) or [])]
    return _read_texts(texts)


def declared_output_paths(step) -> List[str]:
    """The output artifacts this step DECLARES, as relative `data/...` paths.

    Order-preserving, de-duplicated, `[]` for a step that declares none — which is the
    common case, and makes the whole check a no-op for it. A path a prohibition or a
    condition governs is not declared (`read_step_paths`)."""
    return read_step_paths(step).declared


def protected_paths(step) -> List[str]:
    """The `data/...` paths the step says NOT to modify or delete ("do not overwrite
    `data/a.json`", "without modifying `data/a.json`", "leave `data/a.json` untouched").
    Never overlaps `declared_output_paths`."""
    return read_step_paths(step).protected


# ── the two checks ───────────────────────────────────────────────────────────


def _check_declared(declared: List[str], work_dir: Optional[str], manifest, info,
                    history) -> Tuple[List[str], Dict[str, int]]:
    """`(missing, reused)`: the declared paths that do not count as written, and those
    that count because an earlier clean attempt of this step wrote them (path ->
    attempt)."""
    if not declared or not work_dir or not isinstance(manifest, list):
        return [], {}
    names = _am.written_names(manifest)
    root = Path(work_dir).expanduser()
    whole = _am.listing_is_whole(info)
    missing: List[str] = []
    reused: Dict[str, int] = {}
    for path in declared:
        if path in names or Path(path).name in names:
            continue
        unseen = not whole or _am.under_unscanned_dir(path, info)
        if unseen and _am.written_since_start(root / path, info):
            continue
        if history is not None:
            attempt = history.reusable(path, _am.file_signature(root / path))
            if attempt is not None:
                reused[path] = attempt
                continue
        missing.append(path)
    return missing, reused


def missing_declared_outputs(step, work_dir: Optional[str], manifest=None,
                             info: Optional[dict] = None, history=None,
                             waived=()) -> List[str]:
    """Declared output paths that do not count as written (see `declared_output_paths`
    for what counts as declared, and the module docstring for why the manifest rather
    than `Path.exists()` decides).

    A path counts as WRITTEN when the attempt's `data_manifest` reports it, by path or
    by basename — a script that wrote the right content under a sibling name is the step
    evaluator's judgement call, not this one's. Where the manifest could not see the
    path (`info` says the listing is incomplete, or the path is under a symlinked or
    partial directory), a file changed since the attempt started counts (item 5). With
    `history` (the step's `StepHistory`), a path an earlier attempt of the same run of
    the step wrote also counts when that attempt exited 0 and the file is unchanged
    since; a file from before the step began never does. `waived` lists paths dropped
    after a disagreement with the step evaluator. `[]` when there is no declaration, no
    workspace, or no manifest to read: never bounce blind.
    """
    declared = [p for p in declared_output_paths(step) if p not in set(waived or ())]
    return _check_declared(declared, work_dir, manifest, info, history)[0]


def changed_protected_paths(step, manifest=None, info: Optional[dict] = None,
                            waived=()) -> List[Tuple[str, str]]:
    """`(path, "modified" | "deleted")` for each protected path THIS attempt changed.

    Modified: the manifest lists the path with `change == "modified"`, i.e. the file was
    on disk when the attempt started and its signature differs now (a rewrite with the
    same bytes and an atomic replace both count: the sub-task said not to touch it).
    Deleted: `info["deleted"]` lists it. A protected path the attempt CREATED is not
    reported: "do not overwrite" a file that was not there is the step evaluator's to
    read. `[]` without a manifest: never bounce blind."""
    if not isinstance(manifest, list):
        return []
    skip = set(waived or ())
    protected = [p for p in protected_paths(step) if p not in skip]
    if not protected:
        return []
    modified = {str(e.get("path")) for e in manifest
                if isinstance(e, dict) and e.get("change") == "modified"}
    deleted = set((info or {}).get("deleted") or ()) if isinstance(info, dict) else set()
    out: List[Tuple[str, str]] = []
    for path in protected:
        if path in modified:
            out.append((path, "modified"))
        elif path in deleted:
            out.append((path, "deleted"))
    return out


def _why_not_counted(path: str, root: Path, history) -> str:
    """One clause on the copy of a missing declared output that is (or is not) on disk."""
    if not os.path.lexists(root / path):
        return "not on disk"
    rec = history.writer(path) if history is not None else None
    if rec is None:
        return ("on disk, but not written by this attempt or by an earlier attempt of "
                "this step")
    tag, clean = rec
    if not isinstance(tag, int):
        return "on disk, but written by an earlier run of this step, not by this one"
    if clean is not True:
        return (f"on disk, but saved by attempt {tag}, whose script did not exit 0, so "
                f"that copy does not count")
    return f"on disk, but changed after attempt {tag} saved it"


def missing_output_report(missing: List[str], details: Optional[Dict[str, str]] = None) -> str:
    """stderr text for a run that exited 0 without the files its step declared."""
    details = details or {}
    paths = ", ".join(f"`{p}`" + (f" ({details[p]})" if details.get(p) else "")
                      for p in missing)
    return (
        f"The script exited 0 but the output file(s) this sub-task declared were not "
        f"written by this run: {paths}. Checked against the files this attempt actually "
        f"created or modified anywhere in the run directory, so this is a FACT, "
        f"not a review comment. A stale copy that was on disk before this step began "
        f"does not count, because a later step would read the wrong numbers from it. A "
        f"copy saved by an earlier attempt of this step counts only when that attempt's "
        f"script exited 0 and the file has not changed since, so a script that loads "
        f"such a copy and skips the recomputation is accepted; any other copy must be "
        f"written again. "
        f"The step cannot be fulfilled without it. Write each declared path "
        f"exactly as the sub-task spells it (relative to the working directory, so "
        f"`data/<name>`, creating `data/` first if needed), print the path and the byte "
        f"size of every file you save, and keep the analysis you already have — only "
        f"the saving step is missing.\n"
        f"{POSTCONDITION_MARKER} — no model was involved]"
    )


def _quote(text: Optional[str], limit: int = 400) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def protected_change_report(changed: List[Tuple[str, str]],
                            protected_by: Optional[Dict[str, str]] = None) -> str:
    """stderr text for a run that exited 0 but modified or deleted a file the sub-task
    says to leave as it is. Names each path and the sentence that protects it."""
    protected_by = protected_by or {}
    lines = []
    for path, how in changed:
        line = f"  - `{path}` was {how} by this run."
        if protected_by.get(path):
            line += f" The sub-task says: \"{_quote(protected_by[path])}\""
        lines.append(line)
    removed = any(how == "deleted" for _p, how in changed)
    return (
        "The script exited 0 but changed file(s) this sub-task says to leave as they "
        "are:\n" + "\n".join(lines) + "\n"
        "Checked against the files this attempt created, modified or removed in the run "
        "directory, so this is a FACT, not a review comment. In the next script do not "
        "write to, replace, rename or remove these path(s): open them for reading only "
        "if their content is needed, and save this step's own results under this step's "
        "own path(s). "
        + ("A removed file is not restored by the engine; do not recreate it with "
           "invented content. " if removed else "")
        + "Keep the rest of the analysis as it is.\n"
        f"{POSTCONDITION_MARKER} — no model was involved]"
    )


# ── disagreement with the step evaluator ─────────────────────────────────────

# The verdict says the run wrote the path although the sub-task forbids it.
_DID_WRITE_RE = re.compile(
    r"\b(?:(?:re-?)?wrote|overwrote|(?:re-?)?saved|modified|replaced|touched|deleted"
    r"|removed|changed|updated|regenerated|re-?created|(?:re-?|over)writ(?:es|ing|ten)"
    r"|(?:re-?)?saving|modifying|replacing|deleting)\b", re.IGNORECASE)
_WAS_WRITTEN_RE = re.compile(
    r"[\s`'\"),(*\]]*(?:was|were|is|has\s+been|have\s+been|got)\s+(?:also\s+|again\s+)?"
    r"(?:(?:re-?|over-?)?written|modified|(?:re-?)?saved|replaced|changed|touched|updated"
    r"|deleted|removed|regenerated|re-?created)\b", re.IGNORECASE)
_VIOLATION_RE = re.compile(
    r"violat|contradict|prohibit|forbid|forbidden|not\s+allowed|not\s+permitted"
    r"|must\s+not|should\s+not|protect|must\s+remain|untouched|unchanged|read[- ]only"
    r"|despite|against\s+the", re.IGNORECASE)
# The verdict says the path was not written and should have been. Right after the path
# ("`a` was not updated", "`a` is missing", "`a` must be regenerated") or right before
# it ("did not write `a`", "failed to update the file `a`"). An imperative "do not write
# `a`" is the opposite statement and is not matched.
_WRITTEN_PP = r"(?:re-?)?(?:written|saved|updated|produced|created|generated|refreshed|regenerated|recomputed)"
_AFTER_NOT_WRITTEN_RE = re.compile(
    r"[\s`'\"),(*\]]*(?:"
    r"(?:was|were|is|are|has|have)\s*(?:\s+not|\s+never|n[’']t)\s+(?:been\s+)?" + _WRITTEN_PP
    + r"|(?:must|should|needs?\s+to|has\s+to|have\s+to)\s+be\s+" + _WRITTEN_PP
    + r"|(?:(?:is|was|are|were|remains?)\s+)?(?:still\s+)?"
    r"(?:missing|absent|stale|outdated|not\s+found|out\s+of\s+date)"
    r")\b", re.IGNORECASE)
_BEFORE_NOT_WRITTEN_RE = re.compile(
    r"\b(?:did\s+not|didn[’']t|fail(?:ed|s)?\s+to|never)\s+(?:re-?)?"
    r"(?:wr(?:i|o)te|sav(?:e|ed)|updat(?:e|ed)|produc(?:e|ed)|creat(?:e|ed)|generat(?:e|ed)"
    r"|refresh(?:ed)?|regenerat(?:e|ed))"
    r"(?:\s+(?:" + _DET + r"))*[\s`'\"(\[*]*$", re.IGNORECASE)


def _mentions(sent: str, path: str) -> List["re.Match"]:
    return [m for m in _PATH_RE.finditer(sent) if m.group(1) == path]


def evaluator_objects_to_writing(path: str, unmet, feedback: Optional[str]) -> Optional[str]:
    """The verdict text that objects to `path` having been written, or None.

    Either an unmet requirement that protects the path when read as a sub-task sentence
    ("Do not overwrite or delete `data/a.json`"), or a feedback sentence that protects
    it, or one that says the run wrote it and that this breaks the sub-task ("the run
    also rewrote `data/a.json`, which contradicts the requirement ..."), with no other
    path between the path, the write verb and the objection."""
    for req in unmet or ():
        if isinstance(req, str) and path in _read_texts([req]).protected:
            return req
    for sent in _sentences(feedback or ""):
        hits = _mentions(sent, path)
        if not hits:
            continue
        if path in _read_texts([sent]).protected:
            return sent
        others = [o for o in _PATH_RE.finditer(sent) if o.group(1) != path]

        def clear(a: int, b: int) -> bool:
            """No OTHER path between the two positions: the words belong to this one."""
            lo, hi = min(a, b), max(a, b)
            return not any(lo <= o.start() < hi for o in others)

        for m in hits:
            # "... also overwrote `c`, which violates ..." is about `c`, not about a path
            # named earlier in the sentence.
            if not any(clear(m.start(), v.start()) for v in _VIOLATION_RE.finditer(sent)):
                continue
            verb = None
            for v in _DID_WRITE_RE.finditer(sent, 0, m.start()):
                verb = v
            if verb is not None and clear(verb.start(), m.start()):
                return sent
            if _WAS_WRITTEN_RE.match(sent, m.end()):
                return sent
    return None


def evaluator_requires_writing(path: str, unmet, feedback: Optional[str]) -> Optional[str]:
    """The verdict text that wants `path` written, or None: an unmet requirement that
    declares it when read as a sub-task sentence, or a feedback sentence that says, right
    next to the path, that it was not written, is missing or stale, or must be written."""
    for req in unmet or ():
        if isinstance(req, str) and path in _read_texts([req]).declared:
            return req
    for sent in _sentences(feedback or ""):
        for m in _mentions(sent, path):
            if (_AFTER_NOT_WRITTEN_RE.match(sent, m.end())
                    or _BEFORE_NOT_WRITTEN_RE.search(sent[:m.start()])):
                return sent
    return None


def _conflict_note(path: str, kind: str, bounced_at: List[int], attempt, quote: str) -> str:
    """What the engineer is told once: both messages, and which one stands."""
    earlier = ("attempts " if len(bounced_at) > 1 else "attempt ") + ", ".join(
        str(a) for a in bounced_at)
    if kind == "declared":
        mine = f"sent {earlier} back for NOT writing `{path}`"
        theirs = f"rejected attempt {attempt}, which wrote it"
        now = f"no longer requires `{path}`"
        drop = "\"declared output(s) not written\""
    else:
        mine = f"sent {earlier} back for changing `{path}`"
        theirs = f"rejected attempt {attempt}, which left it as it was"
        now = f"no longer treats `{path}` as a file to leave unchanged"
        drop = "\"changed file(s) this sub-task says to leave as they are\""
    return (
        f"**Two checks gave opposite instructions about `{path}`. The step evaluator's "
        f"reading stands.**\n"
        f"  - The automatic post-run check (a pattern match on the sub-task text, no "
        f"model) {mine}.\n"
        f"  - The step evaluator, which reads the sub-task sentence itself, {theirs}: "
        f"\"{_quote(quote)}\"\n"
        f"For the rest of this step the post-run check {now}. Follow the step "
        f"evaluator's feedback about this file and disregard any earlier {drop} message "
        f"that names it. Every other requirement of the sub-task still applies."
    )


def arbitrate_with_step_evaluator(state, verdict, ctx=None) -> dict:
    """State updates after a step-evaluator REJECTION whose text conflicts with this
    module's reading of a path; `{}` when there is no conflict (the usual case).

    declared P, the attempt wrote P, the verdict objects to that write  → P is no
        longer required;
    protected P, the attempt left P alone, the verdict wants it written → P is no
        longer protected.
    The waiver lasts for the rest of the step (`output_postcondition_waived`). When an
    earlier attempt was bounced here over the same path, `output_conflict_note` gives the
    engineer both messages and says the evaluator's stands; when the evaluator spoke
    first, the waiver alone prevents the bounce. Never raises past its caller's guard,
    and does nothing with the check off or under a remote executor."""
    if ctx is not None and not getattr(ctx, "check_declared_outputs", True):
        return {}
    if os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR"):
        return {}
    manifest = state.get("data_manifest")
    if not isinstance(manifest, list):
        return {}
    paths = read_step_paths(state.get("step"))
    if not paths.declared and not paths.protected:
        return {}
    waived = state.get("output_postcondition_waived") or {}
    w_declared = list(waived.get("declared") or [])
    w_protected = list(waived.get("protected") or [])
    unmet = list(getattr(verdict, "unmet_requirements", None) or [])
    feedback = getattr(verdict, "feedback", None) or ""
    names = _am.written_names(manifest)
    exact = {str(e.get("path")) for e in manifest if isinstance(e, dict)}
    bounces = state.get("output_postcondition_bounces") or []
    attempt = state.get("attempts", 0)
    notes: List[str] = []
    found = False

    def settle(path: str, kind: str, quote: str, bounced_at: List[int]) -> None:
        nonlocal found
        found = True
        if bounced_at:
            notes.append(_conflict_note(path, kind, bounced_at, attempt, quote))
        was = ("bounced attempt " + ", ".join(map(str, bounced_at)) + " over it"
               if bounced_at else "no attempt was bounced over it yet")
        now = "no longer required" if kind == "declared" else "no longer protected"
        print(f"[output_postcondition] conflict with the step evaluator on {path}: "
              f"evaluator's reading stands ({was}; attempt {attempt} rejected; {now} "
              f"for the rest of the step)", file=sys.stderr, flush=True)

    for path in paths.declared:
        if path in w_declared or not (path in names or Path(path).name in names):
            continue
        quote = evaluator_objects_to_writing(path, unmet, feedback)
        # A verdict that reads both ways about the path settles nothing.
        if quote and not evaluator_requires_writing(path, unmet, feedback):
            w_declared.append(path)
            settle(path, "declared", quote,
                   [b.get("attempt") for b in bounces if path in (b.get("missing") or ())])
    for path in paths.protected:
        if path in w_protected or path in exact:
            continue
        quote = evaluator_requires_writing(path, unmet, feedback)
        if quote and not evaluator_objects_to_writing(path, unmet, feedback):
            w_protected.append(path)
            settle(path, "protected", quote,
                   [b.get("attempt") for b in bounces
                    if any(c and c[0] == path for c in (b.get("changed") or ()))])
    if not found:
        return {}
    out = {"output_postcondition_waived": {"declared": w_declared,
                                           "protected": w_protected}}
    if notes:
        prior = state.get("output_conflict_note")
        out["output_conflict_note"] = "\n\n".join(([prior] if prior else []) + notes)
    return out


def _demote(state: DebugState) -> None:
    """Free the canonical `step_{N}.{py,log}` names for the next attempt while keeping
    this one's artifacts, exactly as `execution_evaluator` does on a failure verdict
    (which does not run on this path). Never raises."""
    try:
        codebase = _codebase_dir(state)
        if codebase is None:
            return
        n = state.get("step_number", 1) or 1
        attempt = state.get("attempts", 0) or 0
        for ext in ("py", "log"):
            canonical = codebase / f"step_{n}.{ext}"
            if canonical.exists():
                canonical.rename(codebase / f"step_{n}_failure_{attempt}.{ext}")
    except Exception as e:  # noqa: BLE001 — bookkeeping must never suppress the bounce
        print(f"[output_postcondition] could not demote the attempt's artifacts "
              f"(non-fatal): {e}", file=sys.stderr, flush=True)


def _step_history(state: DebugState):
    """The step's `StepHistory` as the executor left it after this attempt, or None
    (no history in state, or the file is not this invoke's: then nothing is reusable)."""
    ref = state.get("step_history")
    if not isinstance(ref, dict):
        return None
    try:
        return _am.StepHistory.load(
            ref, _logs_dir(state), state.get("step_number", 1),
            _am.step_key(state.get("step")), state.get("attempts", 0) or 0)
    except Exception:  # noqa: BLE001 — without a history the check is only stricter
        return None


@timed_node("output_postcondition")
def output_postcondition(state: DebugState, runtime: Runtime[PlanContext]) -> DebugState:
    """Check the step's declared outputs count as written and its protected files were
    left alone; turn a clean run that fails either into a failed attempt, with no model
    call.

    Writes `output_postcondition_rejected` on EVERY pass (None when it passes), so a
    bounce never leaks into a later attempt. Never raises: a check that cannot answer
    lets the run through exactly as before.
    """
    ctx = runtime.context
    out: DebugState = {"output_postcondition_rejected": None}
    if not getattr(ctx, "check_declared_outputs", True):
        return out
    # Guest-side data: engine-side absence proves nothing (see the module docstring).
    if os.environ.get("CMBAGENT_LG_REMOTE_EXECUTOR"):
        return out
    step = state.get("step")
    n = state.get("step_number", 1) or 1
    attempt = state.get("attempts", 0) or 0
    try:
        paths = read_step_paths(step)
    except Exception as e:  # noqa: BLE001 — a post-condition must never break the graph
        print(f"[output_postcondition] check skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return out
    # Once per step: which paths the text names in a way this check does not parse.
    if paths.unsure and not state.get("output_postcondition_unsure_logged"):
        for path, why in paths.unsure.items():
            print(f"[output_postcondition] step {n}: not sure whether {path} is a "
                  f"declared output or a protected file ({why}) → treated as neither",
                  file=sys.stderr, flush=True)
        out["output_postcondition_unsure_logged"] = True
    # Only a run that otherwise succeeded — a traceback already explains a missing file.
    if state.get("execution_returncode") != 0 or state.get("execution_timed_out"):
        return out
    waived = state.get("output_postcondition_waived") or {}
    manifest, info = state.get("data_manifest"), state.get("data_manifest_info")
    try:
        history = _step_history(state)
        declared = [p for p in paths.declared if p not in (waived.get("declared") or ())]
        missing, reused = _check_declared(declared, state.get("work_dir"), manifest, info,
                                          history)
        changed = changed_protected_paths(step, manifest, info,
                                          waived=waived.get("protected"))
        details = {}
        if missing and state.get("work_dir"):
            root = Path(state["work_dir"]).expanduser()
            details = {p: _why_not_counted(p, root, history) for p in missing}
    except Exception as e:  # noqa: BLE001 — a post-condition must never break the graph
        print(f"[output_postcondition] check skipped (non-fatal): {e}",
              file=sys.stderr, flush=True)
        return out
    if reused:
        print("[output_postcondition] declared output(s) not rewritten by this attempt "
              "but saved by an earlier attempt of this step that exited 0, unchanged "
              "since: "
              + ", ".join(f"{p} (attempt {k})" for p, k in reused.items())
              + " → counted as written", file=sys.stderr, flush=True)
    if not missing and not changed:
        return out

    reports, summaries, kinds = [], [], []
    if missing:
        reports.append(missing_output_report(missing, details))
        summaries.append("declared output(s) not written by this run: " + ", ".join(missing))
        kinds.append("missing_output")
        print(f"[output_postcondition] clean run, declared output(s) not written by this "
              f"attempt: {', '.join(missing)} → bounced with no model call",
              file=sys.stderr, flush=True)
    if changed:
        reports.append(protected_change_report(changed, paths.protected_by))
        listed = ", ".join(f"{p} ({how})" for p, how in changed)
        summaries.append("file(s) the sub-task says to leave as they are were changed by "
                         "this run: " + listed)
        kinds.append("protected_modified")
        print(f"[output_postcondition] clean run, protected file(s) changed by this "
              f"attempt: {listed} → bounced with no model call",
              file=sys.stderr, flush=True)
    report = "\n\n".join(reports)
    summary = "; ".join(summaries)
    stderr = (state.get("execution_stderr") or "")
    _demote(state)
    out.update({
        "output_postcondition_rejected": "+".join(kinds),
        "execution_stderr": (stderr + "\n" if stderr.strip() else "") + report,
        "execution_returncode": 1,
        # The verdict the code gate would have produced, produced here instead: the
        # premise is a `stat()` call, not a judgement. `failure_kind="other"` keeps it
        # away from `escalation`, which only handles missing packages / renamed APIs.
        "current_execution_verdict": ExecutionVerdict(
            status="failure", failure_kind="other",
            error_summary=summary, fix_suggestion=report),
        "error_history": list(state.get("error_history", [])) + [summary],
        # What this step was bounced for, by attempt: read by
        # `arbitrate_with_step_evaluator` when the step evaluator later disagrees.
        "output_postcondition_bounces": list(state.get("output_postcondition_bounces") or [])
        + [{"attempt": attempt, "missing": list(missing),
            "changed": [list(c) for c in changed]}],
    })
    return out


def route_after_output_postcondition(state: DebugState, runtime: Runtime[PlanContext]) -> str:
    """A bounced attempt goes straight back to the engineer (END at the attempt cap);
    anything else continues to the code gate as before.

    No model call and no data-safety branch on this edge — see the module docstring for
    why a missing OUTPUT must not be routed through the missing-INPUT layers."""
    if state.get("output_postcondition_rejected"):
        ctx = runtime.context
        if state.get("attempts", 0) >= getattr(ctx, "max_n_attempts", 3):
            return END
        return "engineer"
    return "execution_evaluator"


__all__ = [
    "output_postcondition",
    "route_after_output_postcondition",
    "StepPaths",
    "read_step_paths",
    "declared_output_paths",
    "protected_paths",
    "missing_declared_outputs",
    "changed_protected_paths",
    "missing_output_report",
    "protected_change_report",
    "evaluator_objects_to_writing",
    "evaluator_requires_writing",
    "arbitrate_with_step_evaluator",
    "POSTCONDITION_MARKER",
]
