"""ReAct demo — the loop, and how `temperature` affects it.

A from-scratch LangGraph ReAct agent (two nodes, `agent` and `tools`, plus a
conditional edge) rather than `create_react_agent`, repeated N times to measure
the tool-call rate. The default question is one the model already knows the
answer to, so it can choose priors over the lookup.

Usage:
  python examples/react_demo.py --temperature 0.0 --repeat 10
  python examples/react_demo.py --temperature 1.0 --repeat 10
  python examples/react_demo.py --show-trace             # full trace of one run

Optional first positional arg is a work_dir (used as the Langfuse session id
and where the trace_id file is written); defaults to a timestamped path.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Annotated, TypedDict

from dotenv import load_dotenv

load_dotenv(override=True)

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from cmbagent_lg.prompt_utils import flatten_content as _flatten_content

from _common import attach_langfuse, print_trace_info, resolve_work_dir

# ── the one tool ─────────────────────────────────────────────────────────
# Numbers are deliberately off canonical, so a final answer sourced from the
# tool is distinguishable from one sourced from the model's priors.

POPULATIONS = {
    "France": 5_000_000,
    "Germany": 84_000_000,
    "Italy": 59_000_000,
}


@tool
def lookup_population(country: str) -> str:
    """Look up a country's population in an internal reference table."""
    if country in POPULATIONS:
        return f"The population of {country} is {POPULATIONS[country]:,}."
    return f"No data for {country}."


# ── graph state ──────────────────────────────────────────────────────────

class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


# ── graph builder ────────────────────────────────────────────────────────

def make_graph(temperature: float):
    model = ChatGoogleGenerativeAI(
        model=os.environ.get("REACT_MODEL", "gemini-3.1-flash-lite"),
        temperature=temperature,
        thinking_level="low",
        google_api_key=os.environ["GOOGLE_API_KEY"],
    ).bind_tools([lookup_population])

    def agent_node(state: State):
        return {"messages": [model.invoke(state["messages"])]}

    def tool_node(state: State):
        last = state["messages"][-1]
        outs = []
        for tc in last.tool_calls:
            result = lookup_population.invoke(tc["args"])
            outs.append(ToolMessage(content=result, tool_call_id=tc["id"]))
        return {"messages": outs}

    def should_continue(state: State):
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    g = StateGraph(State)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


# ── runner ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a knowledgeable assistant. You must use your tool"
)


def run_once(graph, question: str, config: dict | None = None) -> dict:
    final = graph.invoke(
        {"messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=question),
        ]},
        config=config,
    )
    msgs = final["messages"]
    tool_called = any(getattr(m, "tool_calls", None) for m in msgs)
    final_text = _flatten_content(getattr(msgs[-1], "content", ""))
    return {"tool_called": tool_called, "final_text": final_text, "messages": msgs}


def print_trace(messages):
    """Pretty-print the full ReAct trace of one run — for live demos."""
    for i, m in enumerate(messages):
        role = m.__class__.__name__.replace("Message", "")
        body = _flatten_content(getattr(m, "content", "")).strip()
        tcs = getattr(m, "tool_calls", None) or []
        print(f"\n[{i}] {role}")
        if body:
            print(f"    {body}")
        for tc in tcs:
            print(f"    → tool_call: {tc['name']}({tc['args']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir", nargs="?", default=None,
                    help="Optional work_dir (used as Langfuse session id).")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--question", default="What is the population of France?")
    ap.add_argument("--show-trace", action="store_true",
                    help="Print one full message trace then exit.")
    args = ap.parse_args()

    # ── workdir + langfuse ───────────────────────────────────────────────
    fake_argv = [sys.argv[0]] + ([args.work_dir] if args.work_dir else [])
    work_dir = resolve_work_dir(fake_argv)
    handler, callbacks = attach_langfuse()

    tags = ["react_demo", f"T={args.temperature}", work_dir.name]
    base_metadata = {"langfuse_session_id": work_dir.name, "langfuse_tags": tags}

    def make_config(label: str) -> dict:
        return {
            "callbacks": callbacks,
            "run_name": f"react_demo · T={args.temperature} · {label}",
            "tags": tags,
            "metadata": base_metadata,
        }

    graph = make_graph(args.temperature)

    if args.show_trace:
        print(f"\n=== Trace (T={args.temperature}) ===\nQ: {args.question}")
        out = run_once(graph, args.question, config=make_config("show-trace"))
        print_trace(out["messages"])
        print_trace_info(handler, work_dir)
        return

    print(f"\n=== ReAct demo — T={args.temperature}, n={args.repeat} ===")
    print(f"Q: {args.question}\n")

    tool_uses = 0
    for i in range(args.repeat):
        out = run_once(
            graph, args.question, config=make_config(f"run {i+1}/{args.repeat}")
        )
        tool_uses += int(out["tool_called"])
        flag = "TOOL" if out["tool_called"] else "SKIP"
        snippet = out["final_text"].replace("\n", " ").strip()[:120]
        print(f"  run {i+1:>2}  [{flag}]  {snippet}")

    print(f"\nTool-call rate: {tool_uses}/{args.repeat} = {tool_uses/args.repeat:.0%}")
    print_trace_info(handler, work_dir)


if __name__ == "__main__":
    main()
