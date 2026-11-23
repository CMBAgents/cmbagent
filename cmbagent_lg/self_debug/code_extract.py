"""Deterministic extraction of the engineer's script from its answer, so the formatter model is not asked to copy a
whole script into a JSON string.

`engineer.yaml` fixes the layout: `**Code Explanation:**`, `**Modifications:**` (retries), `**Python Code:**` and one
fenced python block. `extract_engineer_response` reads it:

1. an answer that is itself a JSON object with `python_code` (an engineer that answered in the schema's shape) is
   read as that object;
2. otherwise the fenced block after the last `**Python Code:**`, whose closing fence is a line on its own, else the
   last ```python block anywhere;
3. no fence at all: None, and the caller asks the formatter.

`normalise_code` repairs a script that arrives as one JSON-escaped line (literal `\\n` and `\\"`, no real newline),
which is what a run on rusty executed as a 0-line `step_1_failure_1.py` (b13-1).
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from typing import Optional

from cmbagent_lg.self_debug.schemas import EngineerResponse

_PY_LANGS = ("python", "py", "python3")
# A fence line: optional indentation, three or more backticks, an optional language tag, nothing else.
_FENCE_LINE = re.compile(r"^[ \t]*(`{3,})[ \t]*([A-Za-z0-9_+.-]*)[ \t]*$")
_HEADER = {
    "explanation": re.compile(r"\*\*\s*Code Explanation\s*:?\s*\*\*:?", re.I),
    "modifications": re.compile(r"\*\*\s*Modifications\s*:?\s*\*\*:?", re.I),
    "code": re.compile(r"\*\*\s*Python Code\s*:?\s*\*\*:?", re.I),
}
_NO_MODIFICATIONS = re.compile(r"^\(?\s*(none|n/?a|omit(ted)?\b.*|not applicable|first attempt.*)\s*\)?\.?$", re.I)


def _parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError):
        return False


def normalise_code(code: str) -> str:
    """The script, ending in one newline. A script that does not parse is repaired when that is unambiguous: one line
    holding literal `\\n` escapes is JSON-decoded, an indented block is dedented; each repair is kept only if the result
    parses. Anything else is returned as it came (static_preflight then refuses it as a failed attempt)."""
    s = (code or "").strip("\n")
    if _parses(s):
        return s + "\n"
    one_line = s.strip()
    if "\n" not in one_line and "\\n" in one_line:
        literal = one_line if (len(one_line) >= 2 and one_line[0] == one_line[-1] == '"') else '"' + one_line + '"'
        try:
            decoded = json.loads(literal)
        except ValueError:
            decoded = None
        if isinstance(decoded, str) and _parses(decoded.strip("\n")):
            return decoded.strip("\n") + "\n"
    dedented = textwrap.dedent(s)
    if dedented != s and _parses(dedented):
        return dedented + "\n"
    return s + "\n"


def _fenced_blocks(text: str):
    """[(lang, start offset of the opening fence, code)] for every fenced block. The closing fence is a fence line of
    at least the opening's backticks with no language tag; an unterminated block runs to the end of the text."""
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)
    blocks, i = [], 0
    while i < len(lines):
        m = _FENCE_LINE.match(lines[i].rstrip("\r\n"))
        if not m:
            i += 1
            continue
        ticks, lang = len(m.group(1)), m.group(2).lower()
        j = i + 1
        while j < len(lines):
            c = _FENCE_LINE.match(lines[j].rstrip("\r\n"))
            if c and not c.group(2) and len(c.group(1)) >= ticks:
                break
            j += 1
        blocks.append((lang, offsets[i], "".join(lines[i + 1:j])))
        i = j + 1
    return blocks


def _section(text: str, name: str, end: int) -> Optional[str]:
    """The text after header `name` up to the next header or `end`; None when the header is absent."""
    m = None
    for m in _HEADER[name].finditer(text, 0, end):
        pass
    if m is None:
        return None
    stop = end
    for other, rx in _HEADER.items():
        if other == name:
            continue
        n = rx.search(text, m.end(), end)
        if n:
            stop = min(stop, n.start())
    return text[m.end():stop].strip()


def _from_json(raw: str) -> Optional[EngineerResponse]:
    s = raw.strip()
    blocks = _fenced_blocks(s)
    if len(blocks) == 1 and blocks[0][0] == "json":
        s = blocks[0][2].strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("python_code"), str):
        return None
    mods = obj.get("modification_summary")
    return EngineerResponse(python_code=normalise_code(obj["python_code"]),
                            code_explanation=str(obj.get("code_explanation") or ""),
                            modification_summary=str(mods) if mods else None)


def extract_engineer_response(raw: str) -> tuple[Optional[EngineerResponse], str]:
    """(response, how) from the engineer's answer, or (None, "no fence") when the formatter is needed. `how` names the
    rule that applied, for the log."""
    raw = raw or ""
    obj = _from_json(raw)
    if obj is not None:
        return obj, "a JSON object with python_code"
    blocks = _fenced_blocks(raw)
    header = None
    for header in _HEADER["code"].finditer(raw):
        pass
    chosen, how = None, ""
    if header is not None:
        for lang, start, code in blocks:
            if start >= header.end() and lang in ("",) + _PY_LANGS:
                chosen, how = (start, code), "the fenced block after **Python Code:**"
                break
    if chosen is None:
        py = [(start, code) for lang, start, code in blocks if lang in _PY_LANGS]
        if py:
            chosen, how = py[-1], "the last python fenced block"
    if chosen is None:
        return None, "no fence"
    start, code = chosen
    explanation = _section(raw, "explanation", start)
    if explanation is None:                       # no header: the prose before the code, minus the headers
        explanation = _HEADER["code"].sub("", _HEADER["modifications"].sub("", raw[:start])).strip()
    mods = _section(raw, "modifications", start)
    if mods is not None and (not mods or _NO_MODIFICATIONS.match(mods)):
        mods = None
    return EngineerResponse(python_code=normalise_code(code), code_explanation=explanation,
                            modification_summary=mods), how
