"""Live check of the OpenAI-compatible endpoints in the registry named by LLM_ENDPOINTS: for each model, the three
things cmbagent_lg asks of a model.

    LLM_ENDPOINTS=~/.config/llm/llm_endpoints.json python tests/check_endpoints.py [model ...]

1. chat: a plain answer;
2. structured: a Pydantic answer through with_structured_output (the formatter, verdicts, plans);
3. tools: a tool call, the tool's result sent back as a ToolMessage, then a final answer (the ReAct researcher).
Each line reports pass or the error; keys are never printed. Makes a few small requests per model.
"""
import sys
import time

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cmbagent_lg import llms


class Verdict(BaseModel):
    """A step verdict."""
    passed: bool = Field(description="whether the step met its goal")
    reason: str = Field(description="one sentence")


@tool
def read_file(path: str) -> str:
    """Return the contents of a file in the workspace."""
    return "omega_m = 0.3111\nsigma_8 = 0.8102\n"


def check(name):
    out = []
    m = llms.chat_model(name, "critic")
    t0 = time.time()
    try:
        r = m.invoke([HumanMessage("In one sentence: what does sigma_8 measure?")])
        out.append("chat ok (%d chars)" % len(r.content or ""))
    except Exception as e:                                          # noqa: BLE001
        out.append("chat FAILED: %s" % str(e)[:160])
    try:
        v = m.with_structured_output(Verdict).invoke(
            [HumanMessage("A step was asked to write data/fit.csv; the log shows it wrote data/fit.csv with 200 rows. "
                          "Did the step meet its goal?")])
        out.append("structured ok (passed=%s)" % v.passed if isinstance(v, Verdict) else "structured FAILED: got %r" % (v,))
    except Exception as e:                                          # noqa: BLE001
        out.append("structured FAILED: %s" % str(e)[:160])
    try:
        mt = m.bind_tools([read_file])
        msgs = [HumanMessage("Use the read_file tool to read params.txt, then tell me the value of sigma_8.")]
        a = mt.invoke(msgs)
        calls = getattr(a, "tool_calls", None) or []
        if not calls:
            out.append("tools FAILED: no tool call (answer: %r)" % (a.content or "")[:80])
        else:
            msgs += [a, ToolMessage(read_file.invoke(calls[0]["args"]), tool_call_id=calls[0]["id"])]
            b = mt.invoke(msgs)
            out.append("tools ok (final answer %s 0.8102)" % ("contains" if "0.81" in (b.content or "") else "LACKS"))
    except Exception as e:                                          # noqa: BLE001
        out.append("tools FAILED: %s" % str(e)[:160])
    return "%-22s %s  [%.0f s]" % (name, "; ".join(out), time.time() - t0)


if __name__ == "__main__":
    names = sys.argv[1:] or list(llms.endpoints())
    if not names:
        sys.exit("no endpoints: set LLM_ENDPOINTS to the registry file")
    for n in names:
        print(check(n), flush=True)
