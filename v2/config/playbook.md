# AI Trading Playbook v2.1 — INTELLIGENCE LAYER
# Updated by: AI Agent (self)
# Last updated: 2026-06-04

## Architecture
This is an LLM-AUGMENTED trading system, NOT a deterministic bot.
- Deterministic code: data, signal math, risk, execution, journaling
- LLM intelligence: qualitative signal review, macro analysis, stock discovery
- DreamPod: overnight analysis engine that preps the next session
- Discovery Engine: news + supply-chain + corruption-aware thematic stock discovery

## Current Market Regime
- US equities: trending (bullish bias)
- Volatility: moderate (VIX ~15-20)
- Macro: Fed pause narrative, disinflationary

## Active Theses
1. Momentum breakout on strong volume works in trending regime
2. AI infrastructure power plays (VST, CEG) are structural longs
3. India FDI data center theme: ADANIGREEN, RELIANCE, LT
4. Avoid counter-trend trades when VIX elevated

## Watch List
- SPY, QQQ, TSLA, NVDA, META — momentum plays
- AAPL, GOOGL — defensive momentum
- MSFT — AI narrative continued
- VST, CEG — AI power infrastructure (DreamPod discovery)
- ANET, MRVL — AI networking (Discovery engine)

## Avoid List
- Biotech (binary events)
- Low volume < 1M daily
- Earnings week (unless specifically trading earnings)

## Size Adjustments
- Normal: 1% risk per trade
- If VIX > 25: reduce to 0.5% or halt new positions
- If consecutive losses >= 2: reduce to 0.5%

## Discovery Integration
The Discovery Engine (runs every 6h) scans news and maps catalysts to supply chains.
Discovered stocks appear in `data/discovery_briefing.json`.
Adani/Ambani/Gadkari connections in India are scored higher (corruption = contract flow).
Review `data/discovery_memo.md` for new candidates.

## Notes
- System currently in PAPER mode
- LLM Gate reviews every signal before execution — check `data/gate_decisions.jsonl`
- DreamPod runs at 04:00 UTC — review `data/dreampod_memo.md` pre-market
- Update playbook after each trading week
- Update thesis when market regime changes (break of 200D MA, VIX spike, etc.)
