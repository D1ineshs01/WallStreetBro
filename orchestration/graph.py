"""
LangGraph StateGraph definition.

Builds the directed cyclic graph connecting all agents.
Uses PostgreSQL checkpointer for crash-recovery and ASIC audit trail.
"""

import structlog
from langgraph.graph import StateGraph, END

from core.state import AgentState
from orchestration.nodes import (
    execution_node,
    ingestion_node,
    kill_switch_node,
    route_from_supervisor,
    supervisor_node,
    visualization_node,
)

log = structlog.get_logger(__name__)


def build_graph(checkpointer=None):
    """
    Construct and compile the LangGraph StateGraph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g., PostgresSaver).
                      If None, state is held in memory only (dev mode).

    Returns:
        Compiled LangGraph app ready for .invoke() or .astream()
    """
    graph = StateGraph(AgentState)

    # ── Register nodes ─────────────────────────────────────────────────
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("execution", execution_node)
    graph.add_node("visualization", visualization_node)
    graph.add_node("kill_switch_handler", kill_switch_node)

    # ── Entry point ────────────────────────────────────────────────────
    graph.set_entry_point("supervisor")

    # ── Conditional edges from supervisor ──────────────────────────────
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "ingestion": "ingestion",
            "execution": "execution",
            "visualization": "visualization",
            "kill_switch": "kill_switch_handler",
            "end": END,
        },
    )

    # ── All worker nodes return to supervisor ──────────────────────────
    graph.add_edge("ingestion", "supervisor")
    graph.add_edge("execution", "supervisor")
    graph.add_edge("visualization", "supervisor")
    graph.add_edge("kill_switch_handler", END)

    # ── Compile with optional checkpointer ────────────────────────────
    compiled = graph.compile(checkpointer=checkpointer)
    log.info("langgraph_compiled", checkpointer=type(checkpointer).__name__ if checkpointer else "None")
    return compiled


async def get_postgres_checkpointer(database_url: str):
    """
    Create a LangGraph PostgreSQL checkpointer for production use.
    Requires the langgraph-checkpoint-postgres package.
    Falls back to None (in-memory) if unavailable.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        checkpointer = AsyncPostgresSaver.from_conn_string(database_url)
        await checkpointer.setup()
        log.info("postgres_checkpointer_ready")
        return checkpointer
    except ImportError:
        log.warning(
            "postgres_checkpointer_unavailable",
            hint="pip install langgraph-checkpoint-postgres",
        )
        return None
    except Exception as exc:
        log.error("postgres_checkpointer_error", error=str(exc))
        return None
