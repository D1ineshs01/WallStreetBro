# Wall Street Bro — Daily Cost Analysis
*Prices sourced March 2026. All USD.*

---

## API Pricing Reference

### xAI Grok (grok-4.1-fast)
| Token Type | Per 1M tokens |
|---|---|
| Input (uncached) | $0.20 |
| Input (cached — static system prompt) | $0.05 |
| Output | $0.50 |

### Anthropic Claude
| Model / Token Type | Per 1M tokens |
|---|---|
| Sonnet 4.5 — Input uncached | $3.00 |
| Sonnet 4.5 — Input cached (cache hit) | $0.30 |
| Sonnet 4.5 — Output | $15.00 |
| Opus 4.6 — Input uncached | $5.00 |
| Opus 4.6 — Input cached (cache hit) | $0.50 |
| Opus 4.6 — Output | $25.00 |

### Alpaca
| Tier | Cost |
|---|---|
| Paper trading | **Free** |
| Live trading (commission) | **Free** |
| Basic market data (IEX, delayed) | **Free** |
| Algo Trader Plus (real-time, all exchanges) | $99/month |

---

## Token Usage Per Agent Cycle

### Grok Scanner (4 concurrent scan types per cycle)
| Component | Tokens |
|---|---|
| System prompt — static, cached | 1,500 × 4 = 6,000 |
| User message — dynamic, uncached | 300 × 4 = 1,200 |
| Output per scan type | 800 × 4 = 3,200 |

### Claude Sonnet — Supervisor (~4 routing calls per full cycle)
| Component | Tokens |
|---|---|
| State summary — mostly cached | 1,000 × 4 = 4,000 |
| State diff — uncached (changes each call) | 200 × 4 = 800 |
| Route decision output (tool_use block) | 150 × 4 = 600 |

### Claude Opus — Execution Agent (per trade)
| Component | Tokens |
|---|---|
| Signal + account data input | ~8,000 |
| Multi-turn tool call output | ~1,500 |

---

## Scenario Breakdown

### Scenario 1 — AGGRESSIVE (15s scan cycle, 24/7)
*Full autonomous operation, continuous scanning, high signal volume*

| Component | Cycles/Calls/Day | Daily Cost |
|---|---|---|
| Grok — cached input | 34,560,000 tokens | $1.73 |
| Grok — uncached input | 6,912,000 tokens | $1.38 |
| Grok — output | 18,432,000 tokens | $9.22 |
| **Grok subtotal** | 5,760 scan cycles | **$12.33** |
| Sonnet — cached input | 23,040,000 tokens | $6.91 |
| Sonnet — uncached input | 4,608,000 tokens | $13.82 |
| Sonnet — output | 3,456,000 tokens | $51.84 |
| **Supervisor subtotal** | 23,040 routing calls | **$72.57** |
| Opus — input (20 trades) | 160,000 tokens | $0.80 |
| Opus — output (20 trades) | 30,000 tokens | $0.75 |
| **Execution subtotal** | 20 trades/day | **$1.55** |
| Alpaca Algo Trader Plus | — | $3.30 |
| **TOTAL** | | **$89.75/day** |
| **Monthly estimate** | | **~$2,693/month** |

---

### Scenario 2 — NORMAL (5-min scan, market hours only, 6.5hrs/day)
*Recommended for live trading. Scans every 5 minutes during US market hours only.*

| Component | Cycles/Calls/Day | Daily Cost |
|---|---|---|
| Grok — cached input | 468,000 tokens | $0.023 |
| Grok — uncached input | 93,600 tokens | $0.019 |
| Grok — output | 249,600 tokens | $0.125 |
| **Grok subtotal** | 78 scan cycles | **$0.17** |
| Sonnet — cached input | 312,000 tokens | $0.094 |
| Sonnet — uncached input | 62,400 tokens | $0.187 |
| Sonnet — output | 46,800 tokens | $0.702 |
| **Supervisor subtotal** | 312 routing calls | **$0.98** |
| Opus — input (3 trades) | 24,000 tokens | $0.12 |
| Opus — output (3 trades) | 4,500 tokens | $0.113 |
| **Execution subtotal** | 3 trades/day | **$0.23** |
| Alpaca Algo Trader Plus | — | $3.30 |
| **TOTAL** | | **$4.68/day** |
| **Monthly estimate** | | **~$140/month** |

---

### Scenario 3 — CONSERVATIVE / DEVELOPMENT (15-min scan, market hours)
*For testing, backtesting, and paper trading. Use grok-4.1-fast + Sonnet only.*

| Component | Cycles/Calls/Day | Daily Cost |
|---|---|---|
| Grok — cached input | 156,000 tokens | $0.008 |
| Grok — uncached input | 31,200 tokens | $0.006 |
| Grok — output | 83,200 tokens | $0.042 |
| **Grok subtotal** | 26 scan cycles | **$0.056** |
| Sonnet — cached input | 104,000 tokens | $0.031 |
| Sonnet — uncached input | 20,800 tokens | $0.062 |
| Sonnet — output | 15,600 tokens | $0.234 |
| **Supervisor subtotal** | 104 routing calls | **$0.33** |
| Opus — input (1 trade) | 8,000 tokens | $0.04 |
| Opus — output (1 trade) | 1,500 tokens | $0.038 |
| **Execution subtotal** | 1 trade/day | **$0.08** |
| Alpaca Basic (free data) | — | $0.00 |
| **TOTAL** | | **$0.46/day** |
| **Monthly estimate** | | **~$14/month** |

---

## Key Cost Drivers

### 1. Claude Sonnet output tokens are the #1 cost
The supervisor makes ~4 calls per scan cycle. At **$15.00/1M output tokens**, even short responses (150 tokens) add up fast at high frequencies. In aggressive mode this accounts for **81% of total cost**.

**Fix:** Swap supervisor to a cheaper model for dev. The system prompt in `config/settings.py` already has `SUPERVISOR_MODEL=claude-sonnet-4-5` — you can override this to `claude-haiku-4-5` for testing (~10× cheaper output).

### 2. Scan frequency is the master multiplier
Every 1-minute decrease in scan interval multiplies costs by 1.5–2×. Set `SCAN_INTERVAL_MINUTES` in `.env` to control this.

### 3. Grok output (not input) is the Grok cost driver
At $0.50/1M, output costs 10× more than cached input. Keeping system prompts large and static (so they cache at $0.05/1M) is critical.

### 4. Claude Opus should be used sparingly
Opus at $25/1M output is **66× more expensive** than Haiku. Only use it for actual trade execution — never for scanning or routing.

---

## Cost Optimization Recommendations

| Change | Estimated Saving |
|---|---|
| Use `claude-haiku-4-5` for supervisor during dev | Save ~85% of supervisor cost |
| Scan every 5 min instead of every 15s | Save ~95% vs aggressive |
| Paper trade only (skip Algo Trader Plus) | Save $99/month |
| Switch execution to Sonnet (acceptable for paper) | Save ~70% of execution cost |
| Run only during market hours (6.5h vs 24h) | Save ~73% |

**Recommended dev budget: $0.46–$5/day**
**Recommended live trading budget: $5–$90/day depending on scan frequency**
