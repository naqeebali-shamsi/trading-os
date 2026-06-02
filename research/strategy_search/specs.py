"""Finite, discrete strategy specification grid (no continuous curve fitting)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    family: str
    params: Dict[str, Any]
    param_count: int

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "family": self.family,
            "params": dict(self.params),
            "param_count": self.param_count,
        }


def _ma_specs() -> List[StrategySpec]:
    specs: List[StrategySpec] = []
    fast_opts = (5, 8, 9)
    slow_opts = (13, 21, 34, 55)
    for fast in fast_opts:
        for slow in slow_opts:
            if fast >= slow:
                continue
            sid = f"MA_CROSS_{fast}_{slow}"
            specs.append(
                StrategySpec(
                    strategy_id=sid,
                    family="ma_cross",
                    params={"fast": fast, "slow": slow},
                    param_count=2,
                )
            )
    return specs


def _rsi_specs() -> List[StrategySpec]:
    specs: List[StrategySpec] = []
    for period in (14,):
        for oversold in (25, 30):
            for overbought in (70, 75):
                sid = f"RSI_MR_{period}_{oversold}_{overbought}"
                specs.append(
                    StrategySpec(
                        strategy_id=sid,
                        family="rsi_mean_reversion",
                        params={"period": period, "oversold": oversold, "overbought": overbought},
                        param_count=3,
                    )
                )
    return specs


def _donchian_specs() -> List[StrategySpec]:
    specs: List[StrategySpec] = []
    for period in (20, 55):
        sid = f"DONCHIAN_BREAK_{period}"
        specs.append(
            StrategySpec(
                strategy_id=sid,
                family="donchian_breakout",
                params={"period": period},
                param_count=1,
            )
        )
    return specs


def _pattern_specs(
    *,
    families: tuple[str, ...] | None = None,
    hold_bars_opts: tuple[int, ...] | None = None,
) -> List[StrategySpec]:
    families = families or (
        "engulfing",
        "hammer",
        "shooting_star",
        "harami",
        "three_soldiers_crows",
        "pinbar",
        "marubozu",
    )
    hold_bars_opts = hold_bars_opts or (6, 12)
    specs: List[StrategySpec] = []
    for family in families:
        for hold in hold_bars_opts:
            sid = f"PATTERN_{family.upper()}_{hold}"
            specs.append(
                StrategySpec(
                    strategy_id=sid,
                    family="candle_pattern",
                    params={"pattern_family": family, "hold_bars": hold},
                    param_count=2,
                )
            )
    return specs


def iter_strategy_specs(
    *,
    max_param_count: int | None = None,
    include_patterns: bool = True,
    pattern_families: tuple[str, ...] | None = None,
    pattern_hold_bars: tuple[int, ...] | None = None,
) -> Iterator[StrategySpec]:
    """Yield a fixed catalog of strategies — search breadth is explicit and bounded."""
    catalog = _ma_specs() + _rsi_specs() + _donchian_specs()
    if include_patterns:
        catalog += _pattern_specs(families=pattern_families, hold_bars_opts=pattern_hold_bars)
    for spec in catalog:
        if max_param_count is not None and spec.param_count > max_param_count:
            continue
        yield spec


def spec_count(
    *,
    max_param_count: int | None = None,
    include_patterns: bool = True,
    pattern_families: tuple[str, ...] | None = None,
    pattern_hold_bars: tuple[int, ...] | None = None,
) -> int:
    return sum(
        1
        for _ in iter_strategy_specs(
            max_param_count=max_param_count,
            include_patterns=include_patterns,
            pattern_families=pattern_families,
            pattern_hold_bars=pattern_hold_bars,
        )
    )
