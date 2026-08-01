from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from config import settings
from nodes import (
    node_clone_repository,
    node_fetch_logs_and_alert,
    node_llm_fix_code,
    node_notify_human_escalation,
)
from state import AgentState


def route_clone_outcome(
    state: AgentState,
) -> Literal["notify_human_escalation", "fetch_logs_and_alert"]:
    if state.get("ci_status") == "CANNOT_FIX":
        return "notify_human_escalation"
    return "fetch_logs_and_alert"


def route_fix_outcome(
    state: AgentState,
) -> Literal["notify_human_escalation", "fix_code", "end"]:
    # In the single-pass graph, the externalized loop owns retry decisions.
    # A successful push (FIX_PUSHED) terminates the graph run so the webhook
    # handler can record last_fix_sha on the still-active session; re-running
    # llm_fix_code would re-clone, re-push (non-fast-forward), and destroy the
    # session lineage. PASSED (LLM found nothing to change) also terminates.
    if state.get("ci_status") in ("CANNOT_FIX", "EXHAUSTED", "TIMEOUT", "CANCELLED"):
        return "notify_human_escalation"
    if state.get("ci_status") in ("FIX_PUSHED", "PASSED"):
        return "end"
    attempt = state.get("attempt_count", 1)
    if attempt >= settings.max_retry_attempts:
        state["ci_status"] = "EXHAUSTED"
        return "notify_human_escalation"
    return "fix_code"


def build_graph(checkpointer: Any = None) -> Any:
    builder = StateGraph(AgentState)

    builder.add_node("clone_repository", node_clone_repository)
    builder.add_node("fetch_logs_and_alert", node_fetch_logs_and_alert)
    builder.add_node("llm_fix_code", node_llm_fix_code)
    builder.add_node("notify_human_escalation", node_notify_human_escalation)

    builder.add_edge(START, "clone_repository")

    builder.add_conditional_edges(
        "clone_repository",
        route_clone_outcome,
        {
            "notify_human_escalation": "notify_human_escalation",
            "fetch_logs_and_alert": "fetch_logs_and_alert",
        },
    )

    builder.add_edge("fetch_logs_and_alert", "llm_fix_code")

    builder.add_conditional_edges(
        "llm_fix_code",
        route_fix_outcome,
        {
            "notify_human_escalation": "notify_human_escalation",
            "fix_code": "llm_fix_code",
            "end": END,
        },
    )

    builder.add_edge("notify_human_escalation", END)
    checkpointer = checkpointer or InMemorySaver()
    return builder.compile(checkpointer=checkpointer)
