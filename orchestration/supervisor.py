"""
Supervisor Agent — Claude 4.5 Sonnet.

The supervisor acts as the routing brain of the LangGraph.
It reads the current AgentState and decides which node to activate next.
Uses structured tool calling to guarantee a valid routing decision.
"""

import anthropic
import structlog

from config.settings import settings
from core.exceptions import SupervisorRoutingError
from core.state import AgentState

log = structlog.get_logger(__name__)

# ── Routing tool schema ────────────────────────────────────────────────
ROUTE_DECISION_TOOL = {
    "name": "route_decision",
    "description": (
        "Decide which node to activate next in the trading system. "
        "Choose based on the current state of market events, signals, and risk metrics."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "next_node": {
                "type": "string",
                "enum": ["ingestion", "execution", "visualization", "end"],
                "description": (
                    "ingestion: scan for new market events. "
                    "execution: a validated trade signal is ready to execute. "
                    "visualization: update the dashboard with new data. "
                    "end: no action needed, cycle complete."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "Brief explanation of the routing decision.",
            },
            "urgency": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
                "description": "How urgent is this action.",
            },
        },
        "required": ["next_node", "rationale", "urgency"],
        "additionalProperties": False,
    },
}

SUPERVISOR_SYSTEM_PROMPT = """You are the supervisor of an autonomous financial trading system.
Your only job is to decide which subsystem should act next, based on the current state.

## Decision Rules

1. **Pending trade signals exist** → route to "execution" — always, even during volatile/critical events
2. **CRITICAL or HIGH severity events exist but no signals yet** → route to "ingestion" to generate signals
3. **No recent market data** → route to "ingestion"
4. **Dashboard not updated** → route to "visualization"
5. **All caught up** → route to "end"

## Philosophy
Volatile markets create the BEST trading opportunities — do not avoid them.
Critical events mean bigger price movements, which means better risk/reward setups.
The execution agent is responsible for risk/reward analysis on each individual trade.
Your job is simply to route to "execution" whenever signals exist.

## What to avoid
- NEVER route to "end" if there are unexecuted trade signals
- Do NOT be conservative during volatile periods — route to "execution" aggressively
"""


class SupervisorAgent:
    """
    Routes the LangGraph to the correct next node.
    Returns a dict with at minimum {"next_node": str}.
    """

    def __init__(self):
        self.claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def decide(self, state: AgentState) -> dict:
        """
        Synchronous supervisor decision using Claude.
        Returns partial state update: {"next_node": "...", "messages": [...]}
        """
        state_summary = self._format_state_summary(state)

        response = self.claude.messages.create(
            model=settings.supervisor_model,
            max_tokens=512,
            system=SUPERVISOR_SYSTEM_PROMPT,
            tools=[ROUTE_DECISION_TOOL],
            tool_choice={"type": "tool", "name": "route_decision"},
            messages=[{"role": "user", "content": state_summary}],
        )

        # Extract the tool_use block
        for block in response.content:
            if block.type == "tool_use" and block.name == "route_decision":
                decision = block.input
                next_node = decision["next_node"]
                rationale = decision.get("rationale", "")
                urgency = decision.get("urgency", "low")

                log.info(
                    "supervisor_decision",
                    next_node=next_node,
                    urgency=urgency,
                    rationale=rationale,
                    iteration=state.get("iteration_count", 0),
                )

                return {
                    "next_node": next_node,
                    "messages": [{"role": "assistant", "content": f"[Supervisor] → {next_node}: {rationale}"}],
                }

        raise SupervisorRoutingError("Supervisor did not return a route_decision tool call.")

    def _format_state_summary(self, state: AgentState) -> str:
        events = state.get("market_events", [])
        signals = state.get("trade_signals", [])
        executions = state.get("trade_executions", [])

        recent_events_summary = ""
        if events:
            last = events[-1]
            recent_events_summary = (
                f"Last event: {last.get('category')} / {last.get('disruption_severity')} "
                f"severity — {last.get('summary', '')[:200]}"
            )

        pending_signals = [s for s in signals if s not in [e.get("signal_id") for e in executions]]

        return f"""## Current System State

**Iteration Count:** {state.get('iteration_count', 0)}
**Last Error:** {state.get('error') or 'None'}

**Portfolio:**
- Current Value: ${state.get('current_portfolio_value', 0):,.2f}
- Drawdown: {state.get('drawdown_pct', 0):.1%}

**Pipeline:**
- Market Events Collected: {len(events)}
- {recent_events_summary}
- Unexecuted Trade Signals: {len(pending_signals)}
- Completed Executions This Session: {len(executions)}

Please call route_decision to select the next action."""
