from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from nodes import (
    node_fetch_logs_and_alert,
    node_llm_fix_code,
    node_notify_human_escalation,
    node_notify_success,
    node_poll_ci_status,
)
from state import AgentState


def route_ci_outcome(
    state: AgentState,
) -> Literal["notify_success", "notify_human_escalation", "fix_code"]:
    if state.get("ci_status") == "PASSED":
        return "notify_success"

    attempt = state.get("attempt_count", 1)
    if attempt >= 3:
        return "notify_human_escalation"

    return "fix_code"


def build_graph(checkpointer: Any = None) -> Any:
    builder = StateGraph(AgentState)

    builder.add_node("fetch_logs_and_alert", node_fetch_logs_and_alert)
    builder.add_node("llm_fix_code", node_llm_fix_code)
    builder.add_node("poll_ci_status", node_poll_ci_status)
    builder.add_node("notify_success", node_notify_success)
    builder.add_node("notify_human_escalation", node_notify_human_escalation)

    builder.add_edge(START, "fetch_logs_and_alert")
    builder.add_edge("fetch_logs_and_alert", "llm_fix_code")
    builder.add_edge("llm_fix_code", "poll_ci_status")

    builder.add_conditional_edges(
        "poll_ci_status",
        route_ci_outcome,
        {
            "notify_success": "notify_success",
            "notify_human_escalation": "notify_human_escalation",
            "fix_code": "llm_fix_code",
        },
    )

    builder.add_edge("notify_success", END)
    builder.add_edge("notify_human_escalation", END)

    checkpointer = checkpointer or InMemorySaver()
    return builder.compile(checkpointer=checkpointer)
