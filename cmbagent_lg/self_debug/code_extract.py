"""Deterministic extraction of the engineer's script from its answer, so the formatter model is not asked to copy a
whole script into a JSON string.

`engineer.yaml` fixes the layout: `**Code Explanation:**`, `**Modifications:**` (retries), `**Python Code:**` and one
fenced python block. `extract_engineer_response` reads it:

1. an answer that is itself a JSON object with `python_code` (an engineer that answered in the schema's shape) is
   read as that object;
2. otherwise the first usable python (or bare) fenced block after the last `**Python Code:**` header that is not
   itself inside a fenced block (a script can print markdown holding that header), else the last usable ```python
   block anywhere. The opening fence may stand on the header's own line (`**Python Code:** ```python`); the closing
   fence is a fence line of its own with no language tag. A block is usable when it is not blank and, if no closing
   fence ends it, parses. A block that does not parse is extended to each later bare fence line in turn and the first
   version that parses is kept: a bare ``` line inside a triple-quoted string closed the block early;
3. nothing usable: None, and the caller asks the formatter.

`**Modifications:**` is read before the code block, else after it.

`normalise_code` repairs a script that arrives as one JSON-escaped line (literal `\\n` and `\\"`, no real newline),
which is what a run on rusty executed as a 0-line `step_1_failure_1.py` (b13-1), also when the line is wrapped in
quotes (then it parses, as one string expression, and only the decoded script is a script).
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


def _string_literal_value(code: str) -> Optional[str]:
    """The value of a module that is one string expression and nothing else, else None."""
    try:
        body = ast.parse(code).body
    except (SyntaxError, ValueError):
        return None
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0].value.value
    return None


def normalise_code(code: str) -> str:
    """The script, ending in one newline. A script that does not parse is repaired when that is unambiguous: one line
    holding literal `\\n` escapes is JSON-decoded, an indented block is dedented; each repair is kept only if the result
    parses. One quoted line with `\\n` escapes parses as a lone string expression, so it is decoded too (its value is
    the script). Anything else is returned as it came (static_preflight then refuses it as a failed attempt)."""
    s = (code or "").strip("\n")
    one_line = s.strip()
    escaped_line = "\n" not in one_line and "\\n" in one_line
    if _parses(s) and not (escaped_line and _string_literal_value(one_line) is not None):
        return s + "\n"
    if escaped_line:
        decoded = _string_literal_value(one_line)          # a quoted line: its value (Python or JSON quoting)
        if decoded is None:
            try:
                decoded = json.loads('"' + one_line + '"')
            except ValueError:
                decoded = None
        if isinstance(decoded, str) and "\n" in decoded.strip("\n") and _parses(decoded.strip("\n")):
            return decoded.strip("\n") + "\n"
        if _parses(s):
            return s + "\n"
    dedented = textwrap.dedent(s)
    if dedented != s and _parses(dedented):
        return dedented + "\n"
    return s + "\n"


class _Block:
    """One fenced block: `lang`, `start` (offset of its opening backticks), `code`, and the line indices of the
    opening and closing fences (`close` None when no closing fence ends it)."""
    __slots__ = ("lang", "start", "code", "open", "close", "ticks", "end")

    def __init__(self, lang, start, code, open_, close, ticks, end):
        self.lang, self.start, self.code, self.open, self.close, self.ticks, self.end = \
            lang, start, code, open_, close, ticks, end


def _line_offsets(lines):
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)
    return offsets


def _opening(line: str):
    """(ticks, lang, column of the backticks) when `line` opens a fence: a fence line, or the `**Python Code:**`
    header followed on the same line by a fence. Else None."""
    m = _FENCE_LINE.match(line)
    if m:
        return len(m.group(1)), m.group(2).lower(), m.start(1)
    h = _HEADER["code"].search(line)
    if h:
        m = _FENCE_LINE.match(line[h.end():])
        if m:
            return len(m.group(1)), m.group(2).lower(), h.end() + m.start(1)
    return None


def _is_closing(line: str, ticks: int) -> bool:
    c = _FENCE_LINE.match(line)
    return bool(c and not c.group(2) and len(c.group(1)) >= ticks)


def _fenced_blocks(text: str):
    """Every fenced block, in order. The closing fence is a fence line of at least the opening's backticks with no
    language tag; an unterminated block runs to the end of the text."""
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    blocks, i = [], 0
    while i < len(lines):
        o = _opening(lines[i].rstrip("\r\n"))
        if o is None:
            i += 1
            continue
        ticks, lang, col = o
        j = i + 1
        while j < len(lines) and not _is_closing(lines[j].rstrip("\r\n"), ticks):
            j += 1
        close = j if j < len(lines) else None
        end = offsets[j] + len(lines[j]) if close is not None else len(text)
        blocks.append(_Block(lang, offsets[i] + col, "".join(lines[i + 1:j]), i, close, ticks, end))
        i = j + 1
    return blocks


def _script_parses(code: str) -> bool:
    return _parses(normalise_code(code))


def _usable(b: _Block) -> bool:
    """Can `b` be taken as the script? Not when blank; when no closing fence ends it, only if it parses (an answer cut
    off mid-script goes to the formatter rather than to the executor)."""
    return bool(b.code.strip()) and (b.close is not None or _script_parses(b.code))


def _extend(text: str, b: _Block) -> _Block:
    """`b` extended to the first later closing-shaped fence line that makes it parse, else `b`: a bare ``` line
    inside a triple-quoted string closes the block early."""
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    for k in range(b.close + 1, len(lines)):
        if _is_closing(lines[k].rstrip("\r\n"), b.ticks):
            code = "".join(lines[b.open + 1:k])
            if _script_parses(code):
                return _Block(b.lang, b.start, code, b.open, k, b.ticks, offsets[k] + len(lines[k]))
    return b


def _section(text: str, name: str, end: int, begin: int = 0) -> Optional[str]:
    """The text after the last header `name` in [begin, end) up to the next header or `end`; None when the header is
    absent."""
    m = None
    for m in _HEADER[name].finditer(text, begin, end):
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
    if len(blocks) == 1 and blocks[0].lang == "json":
        s = blocks[0].code.strip()
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
    """(response, how) from the engineer's answer, or (None, "no usable fenced block") when the formatter is needed. `how` names the
    rule that applied, for the log."""
    raw = raw or ""
    obj = _from_json(raw)
    if obj is not None:
        return obj, "a JSON object with python_code"
    blocks = _fenced_blocks(raw)
    lines = raw.splitlines(keepends=True)
    offsets = _line_offsets(lines) + [len(raw)]

    def inside_a_block(pos: int) -> bool:
        return any(offsets[b.open + 1] <= pos < (offsets[b.close] if b.close is not None else len(raw))
                   for b in blocks)

    headers = [h for h in _HEADER["code"].finditer(raw) if not inside_a_block(h.start())]
    chosen, how = None, ""
    for header in reversed(headers):
        for b in blocks:
            if b.start >= header.end() and b.lang in ("",) + _PY_LANGS and _usable(b):
                chosen, how = b, "the fenced block after **Python Code:**"
                break
        if chosen is not None:
            break
    if chosen is None:
        py = [b for b in blocks if b.lang in _PY_LANGS and _usable(b)]
        if py:
            chosen, how = py[-1], "the last python fenced block"
    if chosen is None:
        return None, "no usable fenced block"
    if not _script_parses(chosen.code):
        longer = _extend(raw, chosen)
        if longer is not chosen:
            chosen, how = longer, how + ", extended past a fence line inside the script"
    start = chosen.start
    explanation = _section(raw, "explanation", start)
    if explanation is None:                       # no header: the prose before the code, minus the headers
        explanation = _HEADER["code"].sub("", _HEADER["modifications"].sub("", raw[:start])).strip()
    mods = _section(raw, "modifications", start)
    if mods is None:                              # written after the code block
        mods = _section(raw, "modifications", len(raw), begin=chosen.end)
    if mods is not None and (not mods or _NO_MODIFICATIONS.match(mods)):
        mods = None
    return EngineerResponse(python_code=normalise_code(chosen.code), code_explanation=explanation,
                            modification_summary=mods), how
