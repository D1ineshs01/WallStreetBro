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
from notifications.email_alerts import send_trade_alert

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are an autonomous trade execution agent for Wall Street Bro.
You find and exploit trading opportunities — especially during volatile, uncertain market conditions.

## Your Role
You receive trade signals backed by real-time Grok market intelligence.
Your job is to analyze each signal for risk-to-reward, then execute the best trade.
Volatility and critical events are OPPORTUNITIES, not reasons to pause.

## Workflow (ALWAYS follow this order)
1. Call get_account to check buying power and account status.
2. Call get_quote on the target symbol for current bid/ask.
3. Call get_position on the target symbol to understand existing exposure.
4. Perform risk-to-reward analysis (see below).
5. Call place_order if R:R >= 2:1.
6. After execution, call list_open_orders to confirm the order is registered.

## Risk-to-Reward Analysis (do this on every signal)
Before placing any order, reason through:
- **Catalyst**: What is the specific event driving this move?
- **Direction**: Should this be a buy or sell? Consider both sides — volatile events
  can be traded in either direction. Consider the affected symbol AND related plays:
  - Supply chain disruption → long the commodity, short the affected manufacturer
  - Geopolitical risk → long GLD, TLT, VIX plays; short exposed equities
  - Macro surprise (CPI beat) → short TLT, long financials
  - Sentiment spike → momentum trade in the direction of the spike
- **Entry**: What price level makes sense given current bid/ask?
- **Target**: Where is the realistic profit target? (minimum 2x the risk)
- **Stop**: What invalidates the trade? Set stop at the invalidation level.
- **R:R ratio**: Only execute if reward >= 2x risk.

## Sizing
- Default: 10 shares as a test position to validate the thesis.
- Scale up to 25 shares if confidence > 85% and R:R > 3:1.
- Never exceed max_position_size_usd from account settings.

## Hard Rules (NON-NEGOTIABLE)
- NEVER place an order without first checking buying power.
- For limit orders: buy no more than 1% above ask, sell no less than 1% below bid.
- Always include rationale in the place_order call for the audit log.

## Order Type Selection
- Use "limit" + "day" for most trades to control slippage.
- Use "market" for urgent macro-event trades where speed outweighs slippage cost.

## Mindset
Chaos creates edge. When the market is uncertain, prices move more —
that means bigger profits for well-reasoned trades. Don't hide from volatility, exploit it.
Think step by step. Use all available tools to build conviction before executing.
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
        max_iterations = 3
        for iteration in range(max_iterations):
            response = await self.claude.messages.create(
                model=settings.execution_model,
                max_tokens=1500,
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
            send_trade_alert(
                symbol=execution["symbol"],
                side=execution["side"],
                qty=execution["qty"],
                order_type=execution["order_type"],
                limit_price=execution.get("limit_price"),
                status=execution["status"],
                order_id=execution["order_id"],
                rationale=signal.get("rationale", "No rationale provided."),
            )
            return execution

        log.info("no_order_placed", signal_id=signal["signal_id"])
        return None

    def _format_signal_message(self, signal: TradeSignal, state: AgentState) -> str:
        """Format the trade signal into a structured prompt for Claude."""
        return f"""## Trade Signal Received

**Signal ID:** {signal['signal_id']}
**Symbol:** {signal['symbol']}
**Suggested Direction:** {signal['direction'].upper()} (override this if your R:R analysis says otherwise)
**Suggested Quantity:** {signal['suggested_qty']} shares
**Confidence:** {signal['confidence']:.0%}
**Generated By:** {signal['generated_by']}
**Timestamp:** {signal['timestamp']}

### Market Intelligence
{signal['rationale']}

### Current Portfolio State
- Portfolio Value: ${state.get('current_portfolio_value', 0):,.2f}
- Drawdown: {state.get('drawdown_pct', 0):.1%}
### Your Task
1. Check account and get a live quote for {signal['symbol']}.
2. Perform a full risk-to-reward analysis based on the market intelligence above.
3. Consider whether the suggested direction is correct, or if the opposite trade is better.
4. If R:R >= 2:1, place the order. If R:R < 2:1, skip and explain why.
"""
