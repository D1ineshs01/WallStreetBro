"""
JSON Schema contracts for all Alpaca trade execution tools.

These schemas are passed to Claude as tool definitions.
The `additionalProperties: false` constraint prevents hallucinated fields.
Claude is called with tool_choice strict mode to enforce exact schema adherence.
"""

# ── Place Order ────────────────────────────────────────────────────────
PLACE_ORDER_SCHEMA = {
    "name": "place_order",
    "description": (
        "Place a trade order via Alpaca. "
        "Always check account buying power with get_account before calling this."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "pattern": "^[A-Z]{1,5}$",
                "description": "Ticker symbol (uppercase, 1-5 letters). Example: AAPL, SPY, GLD.",
            },
            "qty": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
                "description": "Number of whole shares to trade.",
            },
            "side": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "Trade direction.",
            },
            "type": {
                "type": "string",
                "enum": ["market", "limit", "stop", "stop_limit"],
                "description": "Order type.",
            },
            "time_in_force": {
                "type": "string",
                "enum": ["day", "gtc", "opg", "ioc"],
                "description": "Order duration. 'day'=cancel at end of day, 'gtc'=good till cancelled.",
            },
            "limit_price": {
                "type": ["number", "null"],
                "minimum": 0.01,
                "description": "Required when type='limit' or 'stop_limit'. Omit or null for market/stop.",
            },
            "stop_price": {
                "type": ["number", "null"],
                "minimum": 0.01,
                "description": "Required when type='stop' or 'stop_limit'. Omit or null otherwise.",
            },
            "rationale": {
                "type": "string",
                "description": "Brief explanation of why this trade is being placed (for audit log).",
            },
        },
        "required": ["symbol", "qty", "side", "type", "time_in_force", "rationale"],
        "additionalProperties": False,
    },
}

# ── Cancel Order ───────────────────────────────────────────────────────
CANCEL_ORDER_SCHEMA = {
    "name": "cancel_order",
    "description": "Cancel a specific open order by its Alpaca order ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The Alpaca order UUID to cancel.",
            },
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
}

# ── Cancel All Orders ──────────────────────────────────────────────────
CANCEL_ALL_ORDERS_SCHEMA = {
    "name": "cancel_all_orders",
    "description": "Cancel all open orders. Used by the kill switch.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

# ── Get Account ────────────────────────────────────────────────────────
GET_ACCOUNT_SCHEMA = {
    "name": "get_account",
    "description": (
        "Retrieve current account status including buying power, portfolio value, "
        "cash, and margin. Always call this before placing an order."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

# ── Get Position ───────────────────────────────────────────────────────
GET_POSITION_SCHEMA = {
    "name": "get_position",
    "description": "Get the current open position for a specific symbol.",
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "pattern": "^[A-Z]{1,5}$",
                "description": "Ticker symbol.",
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    },
}

# ── Get Quote ──────────────────────────────────────────────────────────
GET_QUOTE_SCHEMA = {
    "name": "get_quote",
    "description": "Get the latest bid/ask quote for a symbol.",
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "pattern": "^[A-Z]{1,5}$",
                "description": "Ticker symbol.",
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    },
}

# ── List Open Orders ───────────────────────────────────────────────────
LIST_OPEN_ORDERS_SCHEMA = {
    "name": "list_open_orders",
    "description": "List all currently open (unfilled) orders.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

# ── Get Bars (OHLCV) ───────────────────────────────────────────────────
GET_BARS_SCHEMA = {
    "name": "get_bars",
    "description": "Get historical OHLCV bar data for a symbol.",
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "pattern": "^[A-Z]{1,5}$",
            },
            "timeframe": {
                "type": "string",
                "enum": ["1Min", "5Min", "15Min", "1Hour", "1Day"],
                "description": "Bar interval.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": "Number of bars to return.",
            },
        },
        "required": ["symbol", "timeframe"],
        "additionalProperties": False,
    },
}

# ── All tools list for Claude tool_use ────────────────────────────────
ALL_EXECUTION_TOOLS = [
    GET_ACCOUNT_SCHEMA,
    GET_QUOTE_SCHEMA,
    GET_POSITION_SCHEMA,
    GET_BARS_SCHEMA,
    LIST_OPEN_ORDERS_SCHEMA,
    PLACE_ORDER_SCHEMA,
    CANCEL_ORDER_SCHEMA,
    CANCEL_ALL_ORDERS_SCHEMA,
]

# Tool name → schema lookup
TOOL_SCHEMA_MAP = {tool["name"]: tool for tool in ALL_EXECUTION_TOOLS}
