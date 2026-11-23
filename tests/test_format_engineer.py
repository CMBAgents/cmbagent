"""format_engineer reads the engineer's script deterministically and asks the formatter only when there is no fence.

    PYTHONPATH=. python -m pytest tests/test_format_engineer.py
"""
import ast
import json
import types

from cmbagent_lg.context import PlanContext
from cmbagent_lg.self_debug import nodes as sd_nodes
from cmbagent_lg.self_debug.code_extract import extract_engineer_response, normalise_code
from cmbagent_lg.self_debug.schemas import EngineerResponse

SCRIPT = '''import numpy as np


def main():
    """Print the mean."""
    x = np.arange(3)
    print("mean", x.mean())


if __name__ == "__main__":
    main()
'''

ANSWER = f"""**Code Explanation:**

Loads nothing; prints the mean of 0, 1, 2.

**Modifications:**

Switched to numpy.

**Python Code:**

```python
{SCRIPT}```
"""


class _Formatter:
    def __init__(self, reply=None):
        self.calls = 0
        self.reply = reply

    def with_structured_output(self, _schema):
        return self

    def invoke(self, msgs, config=None):
        self.calls += 1
        return self.reply or EngineerResponse(python_code=msgs[1].content, code_explanation="from the formatter")


def _run(monkeypatch, raw, formatter=None):
    formatter = formatter or _Formatter()
    monkeypatch.setattr(sd_nodes, "_formatter", lambda _c: formatter)
    rt = types.SimpleNamespace(context=PlanContext(main_task="t"))
    out = sd_nodes.format_engineer({"raw_engineer": raw}, rt)["current_code"]
    return out, formatter.calls


def test_fenced_block_after_header_no_formatter_call(monkeypatch):
    out, calls = _run(monkeypatch, ANSWER)
    assert calls == 0
    assert out.python_code == SCRIPT
    assert out.code_explanation == "Loads nothing; prints the mean of 0, 1, 2."
    assert out.modification_summary == "Switched to numpy."


def test_inner_fence_in_a_string_does_not_cut_the_script():
    script = 'NOTE = """\n```python\nx = 1\n"""\nprint(NOTE)\n'
    raw = "**Code Explanation:**\nx\n\n**Python Code:**\n```python\n" + script + "```\n"
    obj, how = extract_engineer_response(raw)
    assert obj.python_code == script and "after **Python Code:**" in how


def test_header_block_wins_over_sample_output_and_last_python_block_without_header():
    raw = ("**Code Explanation:**\nRuns it.\n\n**Python Code:**\n```python\nprint(1)\n```\n\n"
           "Expected output:\n```\n1\n```\n")
    obj, _ = extract_engineer_response(raw)
    assert obj.python_code == "print(1)\n"
    raw2 = "Here is a draft:\n```python\nprint('old')\n```\nand the final one:\n```python\nprint('new')\n```\n"
    obj2, how2 = extract_engineer_response(raw2)
    assert obj2.python_code == "print('new')\n" and how2 == "the last python fenced block"
    assert obj2.modification_summary is None


def test_omitted_modifications_are_none():
    raw = "**Code Explanation:**\nx\n\n**Modifications:** (omit on first attempt)\n\n**Python Code:**\n```python\nprint(1)\n```"
    obj, _ = extract_engineer_response(raw)
    assert obj.modification_summary is None


def test_json_escaped_one_line_script_is_decoded(monkeypatch):
    escaped = json.dumps(SCRIPT)[1:-1]                 # one line, literal \n and \"
    assert "\n" not in escaped
    out, calls = _run(monkeypatch, "**Python Code:**\n```python\n" + escaped + "\n```\n")
    assert calls == 0 and out.python_code == SCRIPT
    ast.parse(out.python_code)


def test_json_object_reply_is_read(monkeypatch):
    raw = json.dumps({"python_code": SCRIPT, "code_explanation": "prints a mean", "modification_summary": None})
    out, calls = _run(monkeypatch, raw)
    assert calls == 0 and out.python_code == SCRIPT and out.code_explanation == "prints a mean"
    out2, _ = _run(monkeypatch, "```json\n" + raw + "\n```")
    assert out2.python_code == SCRIPT


def test_no_fence_goes_to_the_formatter_and_is_normalised(monkeypatch):
    escaped = json.dumps(SCRIPT)[1:-1]
    f = _Formatter(EngineerResponse(python_code=escaped, code_explanation="x"))
    out, calls = _run(monkeypatch, "Here is my script, explained in prose with no code block.", f)
    assert calls == 1 and out.python_code == SCRIPT


def test_empty_answer_fails_the_attempt_without_a_formatter_call(monkeypatch):
    out, calls = _run(monkeypatch, "   \n")
    assert calls == 0 and "empty answer" in out.python_code


def test_normalise_code_leaves_a_broken_script_alone():
    assert normalise_code("def f(:\n  pass") == "def f(:\n  pass\n"
    assert normalise_code("x = 'a\\nb'") == "x = 'a\\nb'\n"       # parses already: the escape is the script's
    assert normalise_code("    x = 1\n    print(x)\n") == "x = 1\nprint(x)\n"
