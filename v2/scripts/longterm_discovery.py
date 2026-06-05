#!/usr/bin/env python3
"""
scripts/longterm_discovery.py
Weekly Long-Term Value Gem Discovery Runner.

Screens both US/Canada and India markets for quality businesses
trading at reasonable valuations — the Warren Buffett approach.

Usage:
    python3 scripts/longterm_discovery.py
"""
import sys, json, os
from datetime import datetime, timezone

sys.path.insert(0, '/mnt/e/NomadCrew[GROWTH]/trading-os/v2')

from autonome.longterm.us_screener import find_us_gems
from autonome.longterm.india_screener import find_india_gems

INTEL_DIR = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel"


def main():
    os.makedirs(INTEL_DIR, exist_ok=True)
    print("=" * 60)
    print("LONG-TERM VALUE GEM DISCOVERY")
    print("=" * 60)

    # ── US / Canada ──
    print("\n[1] Screening US & Canada...")
    us_gems = find_us_gems(min_total=8, max_results=25, sample_size=80)
    print(f"     Found {len(us_gems)} US/Canada gems")

    # ── India ──
    print("\n[2] Screening India...")
    india_gems = find_india_gems(min_total=8, max_results=25, sample_size=80)
    print(f"     Found {len(india_gems)} India gems")

    # ── Save ──
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "us_gems": {
            "count": len(us_gems),
            "gems": us_gems,
        },
        "india_gems": {
            "count": len(india_gems),
            "gems": india_gems,
        },
    }

    path = f"{INTEL_DIR}/longterm_gems.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Markdown report ──
    md_path = f"{INTEL_DIR}/longterm_report.md"
    with open(md_path, "w") as f:
        f.write(f"""# Long-Term Value Gems Report
Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## 🇺🇸 US & Canada ({len(us_gems)} gems)

| Symbol | Score | Q | V | G | R | PE | ROE | D/E | 52w | Cap |
|--------|-------|---|---|---|---|----|-----|-----|-----|-----|
""")
        for g in us_gems[:15]:
            rec = "STRONG_BUY" if g['total_score'] >= 14 else "BUY" if g['total_score'] >= 10 else "HOLD"
            f.write(f"| {g['display_symbol']} | **{g['total_score']}** {rec} | {g['quality_score']} | {g['value_score']} | {g['growth_score']} | {g['risk_score']} | {g['pe_fmt']} | {g['roe_fmt']} | {g['de_fmt']} | {g['52w_range_fmt']} | {g['mkt_cap_fmt']} |\n")

        f.write(f"""
## 🇮🇳 India ({len(india_gems)} gems)

| Symbol | Score | Q | V | G | R | PE | ROE | D/E | 52w | Cap |
|--------|-------|---|---|---|---|----|-----|-----|-----|-----|
""")
        for g in india_gems[:15]:
            rec = "STRONG_BUY" if g['total_score'] >= 14 else "BUY" if g['total_score'] >= 10 else "HOLD"
            f.write(f"| {g['display_symbol']} | **{g['total_score']}** {rec} | {g['quality_score']} | {g['value_score']} | {g['growth_score']} | {g['risk_score']} | {g['pe_fmt']} | {g['roe_fmt']} | {g['de_fmt']} | {g['52w_range_fmt']} | {g['mkt_cap_fmt']} |\n")

    print(f"\n[✓] Saved: {path}")
    print(f"[✓] Report: {md_path}")

    # ── Summary ──
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"US/Canada: {len(us_gems)} gems | India: {len(india_gems)} gems")
    if us_gems:
        print(f"Top US: {us_gems[0]['display_symbol']} (score {us_gems[0]['total_score']})")
    if india_gems:
        print(f"Top India: {india_gems[0]['display_symbol']} (score {india_gems[0]['total_score']})")


if __name__ == "__main__":
    main()
