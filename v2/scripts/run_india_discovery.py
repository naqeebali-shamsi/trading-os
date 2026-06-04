#!/usr/bin/env python3
"""
scripts/run_india_discovery.py
India stock discovery + value screening runner.

Usage:
    python3 scripts/run_india_discovery.py

Outputs:
    swarm/intel/india_value_picks.json   — Top value picks
    swarm/intel/india_macro.json         — Macro risk snapshot
    swarm/intel/india_report.md          — Human-readable report
"""
import sys, os, json, logging
from datetime import datetime, timezone

# Add paths
sys.path.insert(0, "/mnt/e/NomadCrew[GROWTH]/trading-os/v2")
sys.path.insert(0, "/mnt/e/NomadCrew[GROWTH]/trading-os/timesfm_env/lib/python3.11/site-packages")

from autonome.india.fundamentals import find_value_picks, INDIA_UNIVERSE
from autonome.india.sentinel import IndiaSentinel, write_sentinel_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("india.runner")

INTEL_DIR = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel"


def run():
    log.info("=== India Discovery Run ===")

    # 1. Macro sentinel
    sentinel = IndiaSentinel()
    macro = sentinel.scan()
    regime = sentinel.recommend_regime()
    log.info("Macro regime: %s (risk=%.1f)", regime, macro.risk_score)

    # 2. Value screening
    all_symbols = []
    for cat, syms in INDIA_UNIVERSE.items():
        all_symbols.extend(syms)
    all_symbols = list(set(all_symbols))

    log.info("Screening %d stocks...", len(all_symbols))
    picks = find_value_picks(symbols=all_symbols, min_value_score=5.5)
    log.info("Found %d value picks", len(picks))

    # 3. Filter by regime
    if regime == "DEFENSE":
        picks = [p for p in picks if p.is_in_dip and p.fundamental_score() >= 7.0]
        log.info("Defense mode: filtered to %d high-conviction dips", len(picks))
    elif regime == "CAUTIOUS":
        picks = [p for p in picks if p.fundamental_score() >= 6.5]
        log.info("Cautious mode: filtered to %d quality picks", len(picks))

    # 4. Write JSON
    os.makedirs(INTEL_DIR, exist_ok=True)
    picks_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "macro_risk": macro.risk_score,
        "count": len(picks),
        "picks": [p.dict() for p in picks[:20]],
    }
    json_path = os.path.join(INTEL_DIR, "india_value_picks.json")
    with open(json_path, "w") as f:
        json.dump(picks_data, f, indent=2, default=str)
    log.info("Written: %s", json_path)

    # 5. Write macro
    write_sentinel_report()

    # 6. Write markdown report
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = os.path.join(INTEL_DIR, f"india_report_{date_str}.md")
    lines = [
        f"# India Value Report — {date_str}",
        f"**Regime**: {regime} | **Macro Risk**: {macro.risk_score}/10",
        f"**USD/INR**: {macro.usd_inr:.2f} | **Oil**: ${macro.oil_usd:.2f}",
        "",
        "## Macro Context",
        f"- {macro.thesis}",
        "",
        f"## Top Value Picks ({len(picks)} found)",
        "",
    ]
    for i, p in enumerate(picks[:15], 1):
        emoji = "🟢" if p.is_in_dip else "🟡" if p.distance_from_52w_low < 0.5 else "🔴"
        lines.append(
            f"{i}. {emoji} **{p.symbol}** — VS={p.value_score():.1f} FS={p.fundamental_score():.1f} "
            f"PE={p.pe_trailing} ROE={p.roe:.1f}% D/E={p.debt_to_equity} "
            f"Price=₹{p.price} (52w: {p.distance_from_52w_low:.0%})"
        )
        lines.append(f"   Sector: {p.sector} | Industry: {p.industry}")
        lines.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    log.info("Report: %s", md_path)

    # Print summary
    print(f"\n=== INDIA DISCOVERY ===")
    print(f"Regime: {regime} | Risk: {macro.risk_score}/10")
    print(f"Picks: {len(picks)}")
    for p in picks[:5]:
        print(f"  {p.symbol:15} VS={p.value_score():.1f} PE={p.pe_trailing} ₹{p.price} dip={p.is_in_dip}")


if __name__ == "__main__":
    run()
