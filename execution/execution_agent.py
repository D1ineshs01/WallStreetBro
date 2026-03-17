"""
Claude Opus 4.6 Execution Agent.

Receives trade signals from the orchestrator, reasons about execution,
and calls Alpaca tools via the MCP server using structured tool calling.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import structlog

from config.settings import settings
from core.exceptions import KillSwitchActivatedError
from core.redis_client import RedisClient
from core.state import AgentState, TradeExecution, TradeSignal
from execution.alpaca_mcp_server import AlpacaMCPServer
from execution.trade_schemas import ALL_EXECUTION_TOOLS

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are an autonomous trade execution agent for Wall Street Bro.

## Your Role
You receive trade signals backed by real-time market intelligence and execute them via Alpaca.

## Workflow (ALWAYS follow this order)
1. Call get_account to check buying power and account status.
2. Call get_quote on the target symbol for current bid/ask.
3. Call get_position on the target symbol to understand existing exposure.
4. If all checks pass, call place_order with appropriate parameters.
5. After execution, call list_open_orders to confirm the order is registered.

## Risk Rules (NON-NEGOTIABLE)
- NEVER execute if the kill switch is active.
- NEVER place an order without first checking buying power.
- NEVER exceed the maximum position size configured in system settings.
- For limit orders, set limit_price no more than 1% above current ask (buy) or below bid (sell).
- Always include a concise rationale in the place_order call for the audit log.

## Order Type Selection
- Use "limit" + "day" for most trades to control slippage.
- Use "market" only for urgent macro-event trades where speed is critical.
- Use "stop" for protective stop-loss orders.

## Response Format
Think step by step. Use tools to gather data before deciding.
After execution, provide a brief summary of what was done and why.
"""


class ExecutionAgent:
    """
    Claude Opus 4.6 translates trade signals into Alpaca orders.
    Uses structured tool calling with pre-defined JSON Schemas.
    """

    def __init__(self, mcp_server: AlpacaMCPServer, redis: RedisClient):
        self.claude = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.mcp = mcp_server
        self.redis = redis

    async def execute_signal(
        self, signal: TradeSignal, state: AgentState
    ) -> Optional[TradeExecution]:
        """
        Process a trade signal through Claude Opus.
        Returns a TradeExecution record if an order was placed, else None.
        """
        # Fast kill switch check before calling LLM
        try:
            from execution.risk import RiskEngine
            enabled = await self.redis.get_execution_status()
            if not enabled:
                raise KillSwitchActivatedError("Kill switch active — skipping signal.")
        except KillSwitchActivatedError as exc:
            log.warning("execution_skipped_kill_switch", signal_id=signal["signal_id"], reason=str(exc))
            return None

        user_message = self._format_signal_message(signal, state)
        messages = [{"role": "user", "content": user_message}]
        order_result = None

        log.info(
            "execution_agent_start",
            signal_id=signal["signal_id"],
            symbol=signal["symbol"],
            direction=signal["direction"],
        )

        # Agentic tool-calling loop
        max_iterations = 10
        for iteration in range(max_iterations):
            response = await self.claude.messages.create(
                model=settings.execution_model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=ALL_EXECUTION_TOOLS,
                messages=messages,
            )

            # Append assistant response to messages
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                log.info("execution_agent_complete", iterations=iteration + 1)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input
                    log.debug("tool_call", tool=tool_name, input=tool_input)

                    # Execute the tool through the MCP server
                    result_str = await self.mcp.handle_tool_call(tool_name, tool_input, state)
                    result_data = json.loads(result_str)

                    # Capture the order if it was placed
                    if tool_name == "place_order" and "order_id" in result_data:
                        order_result = result_data

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})
            else:
                log.warning("unexpected_stop_reason", stop_reason=response.stop_reason)
                break

        if order_result:
            execution = TradeExecution(
                signal_id=signal["signal_id"],
                order_id=order_result["order_id"],
                symbol=order_result["symbol"],
                qty=int(order_result.get("qty", signal["suggested_qty"])),
                side=order_result.get("side", signal["direction"]),
                order_type=order_result.get("type", "market"),
                time_in_force=order_result.get("time_in_force", "day"),
                limit_price=order_result.get("limit_price"),
                status=order_result.get("status", "pending"),
                filled_price=None,
                filled_at=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            log.info(
                "trade_execution_recorded",
                order_id=execution["order_id"],
                symbol=execution["symbol"],
                side=execution["side"],
            )
            return execution

        log.info("no_order_placed", signal_id=signal["signal_id"])
        return None

    def _format_signal_message(self, signal: TradeSignal, state: AgentState) -> str:
        """Format the trade signal into a structured prompt for Claude."""
        return f"""## Trade Signal Received

**Signal ID:** {signal['signal_id']}
**Symbol:** {signal['symbol']}
**Direction:** {signal['direction'].upper()}
**Suggested Quantity:** {signal['suggested_qty']} shares
**Suggested Limit Price:** {signal.get('suggested_limit_price') or 'None (use market price)'}
**Confidence:** {signal['confidence']:.0%}
**Generated By:** {signal['generated_by']}
**Timestamp:** {signal['timestamp']}

### Rationale
{signal['rationale']}

### Current Portfolio State
- Portfolio Value: ${state.get('current_portfolio_value', 0):,.2f}
- Drawdown: {state.get('drawdown_pct', 0):.1%}
- Kill Switch Active: {state.get('kill_switch_active', False)}

Please review this signal, check account status and current quote, then execute if appropriate.
"""
