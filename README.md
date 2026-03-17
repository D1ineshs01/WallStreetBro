# Wall Street Bro — Autonomous AI Trading System

A production-grade, multi-agent autonomous trading system that integrates **xAI Grok**, **Anthropic Claude**, and the **Alpaca brokerage API** into a continuously running intelligence and execution loop.

---

## Architecture

```
xAI Grok (Scanner)          Anthropic Claude (Brain)         Alpaca (Broker)
──────────────────          ────────────────────────         ───────────────
Scans X + web every    →    LangGraph Supervisor routes  →   Paper / Live
15 minutes for:             signals to:                      trade execution
  Supply chain events         Ingestion Node (Grok)          via MCP server
  Geopolitical shifts         Execution Node (Claude)
  Macro data (Fed, CPI)       Visualization Node
  Retail sentiment
        │                           │                               │
        └───────────────────────────┴───────────────────────────────┘
                                    │
                            Redis Pub/Sub (message bus)
                                    │
                            FastAPI + Streamlit Dashboard
                                    │
                            PostgreSQL (audit trail)
                                    │
                            Kill Switch Monitor (background)
```

---

## Key Features

| Feature | Implementation |
|---|---|
| Real-time market scanning | xAI Grok `x_search` + `web_search` tools |
| Multi-agent orchestration | LangGraph `StateGraph` with shared `AgentState` |
| Trade execution | Claude Opus via MCP → Alpaca REST API |
| Live dashboard | FastAPI SSE + Streamlit + Plotly candlestick charts |
| Risk management | Pre-trade checks: drawdown, buying power, position size |
| Kill switch | Redis-backed fail-closed circuit breaker (app + network layer) |
| State persistence | PostgreSQL checkpointer for crash recovery |
| Async event bus | Redis Pub/Sub decouples scanner from execution |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Intelligence | xAI Grok API (`grok-4.1-fast`) |
| Reasoning + Execution | Anthropic Claude (`claude-sonnet-4-5`, `claude-opus-4-6`) |
| Orchestration | LangGraph 1.x |
| Brokerage | Alpaca Trading API via Model Context Protocol (MCP) |
| Backend API | FastAPI + uvicorn |
| Dashboard | Streamlit + Plotly |
| Message Bus | Redis Pub/Sub |
| Database | PostgreSQL + SQLAlchemy async |
| Infrastructure | Docker Compose |
| Language | Python 3.11 |

---

## How It Trades

1. **Grok scans** X (Twitter) and the web every 15 minutes for supply chain disruptions, geopolitical events, macro data, and sentiment shifts
2. **Claude Supervisor** evaluates each event — if confidence > 70% and severity is HIGH or CRITICAL, it routes to the execution node
3. **Claude Execution Agent** calculates risk, formats a JSON-schema-validated order, and sends it to Alpaca via MCP
4. **Kill Switch Monitor** runs every 5 seconds — halts all trading if drawdown breaches 5%, rate limits are hit, or a CRITICAL macro shock is detected
5. **Dashboard** shows live P&L, candlestick charts with trade overlays, Grok intelligence feed, and execution log

---

## Cost to Run (Scenario 3 — Conservative)

| Component | Cost/day |
|---|---|
| xAI Grok (26 scans, 182 tool calls) | ~$0.91 |
| Anthropic Claude (Haiku supervisor + Sonnet execution) | ~$0.46 |
| Alpaca paper trading | Free |
| Redis + PostgreSQL (local Docker) | Free |
| **Total** | **~$1.37/day (~$41/month)** |

---

## Setup

### Prerequisites
- Python 3.11
- Docker Desktop
- xAI API key (`console.x.ai`)
- Anthropic API key (`console.anthropic.com`)
- Alpaca paper trading account (`alpaca.markets`)

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/D1ineshs01/WallStreetBro.git
cd WallStreetBro

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in your API keys

# 4. Start infrastructure
docker-compose up -d redis postgres

# 5. Run the agent + API backend
python main.py --mode all

# 6. Run the dashboard (separate terminal)
streamlit run dashboard/frontend/app.py

# 7. Enable trading (separate terminal, run once)
docker exec -it wsb_redis redis-cli SET agent:execution_status 1
```

Open `http://localhost:8501` to view the dashboard.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```env
# xAI Grok
XAI_API_KEY=your_xai_api_key_here
GROK_MODEL=grok-4.1-fast

# Anthropic Claude
ANTHROPIC_API_KEY=your_anthropic_api_key_here
SUPERVISOR_MODEL=claude-haiku-4-5-20251001
EXECUTION_MODEL=claude-sonnet-4-5

# Alpaca (paper trading by default)
ALPACA_API_KEY=your_alpaca_api_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_key_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Risk limits
MAX_DRAWDOWN_PCT=0.05
MAX_POSITION_SIZE_USD=10000
```

---

## Kill Switch

The system implements multi-layer kill switches per ASIC algorithmic trading guidelines (RG 241):

**Application layer (Redis-backed):**
- Triggers on: >5% portfolio drawdown, rate limit breach, CRITICAL macro event, manual override
- Effect: Sets `agent:execution_status = False` → cancels all open orders → logs to PostgreSQL

**Manual override:**
```bash
# Halt trading immediately
docker exec -it wsb_redis redis-cli SET agent:manual_kill 1

# Resume trading
docker exec -it wsb_redis redis-cli SET agent:execution_status 1
```

The dashboard also has a **HALT TRADING** button for one-click emergency stop.

---

## Project Structure

```
WallStreetBro/
├── main.py                      # Unified entry point
├── config/settings.py           # Pydantic BaseSettings
├── core/                        # Redis client, LangGraph state, exceptions
├── ingestion/grok_agent.py      # Grok x_search + web_search scanner
├── orchestration/               # LangGraph graph, nodes, supervisor
├── execution/                   # Risk engine, MCP server, execution agent
├── dashboard/api/               # FastAPI backend + SSE + WebSocket
├── dashboard/frontend/          # Streamlit UI + Plotly charts
├── kill_switch/monitor.py       # Background safety monitor
├── logging_sinks/postgres_sink.py # SQLAlchemy async models
└── docker-compose.yml           # Redis + PostgreSQL
```

---

## Disclaimer

This project is for **educational and research purposes**. Paper trading only by default. Switch to live trading at your own risk. Past performance of any AI system does not guarantee future results. Always comply with your local financial regulations.
