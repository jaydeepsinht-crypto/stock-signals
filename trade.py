"""Entry, stop, risk/reward, position size, trailing plan (Blueprint sections 45-56, 67-72)."""
from __future__ import annotations

import math

from config import TRADE


def required_mos(total: float) -> float:
    if total >= 85:
        return 0.10
    if total >= 75:
        return 0.15
    if total >= 65:
        return 0.20
    return 0.25


def entry_plan(f, fv: float | None, total: float) -> dict:
    price, atr = f.get("price"), f.get("atr")
    tech_pts = [x for x in (f.get("ma50"), f.get("support20"),
                            (price - atr) if price and atr else None) if x]
    tech_entry = sum(tech_pts) / len(tech_pts) if tech_pts else None
    fv_entry = fv * (1 - required_mos(total)) if fv else None
    if fv_entry and tech_entry:
        entry = 0.6 * fv_entry + 0.4 * tech_entry
    else:
        entry = fv_entry or tech_entry
    if not entry:
        return {}
    z = TRADE["entry_zone"]
    return {"fv_entry": fv_entry, "tech_entry": tech_entry, "entry": entry,
            "zone_low": entry * (1 - z), "zone_high": entry * (1 + z), "mos_required": required_mos(total)}


def stop_loss(f, entry: float) -> float:
    atr = f.get("atr") or 0.0
    support = f.get("support20") or entry
    stop = min(support - atr, entry - 2 * atr)
    floor = entry * (1 - TRADE["max_stop_distance"])
    if stop < floor or stop <= 0:
        stop = floor
    if stop >= entry:  # support above planned entry (deep pullback entry): use volatility stop
        stop = max(entry - 2 * atr, floor)
    return stop


def risk_reward(entry, stop, target):
    if not (entry and stop and target) or entry <= stop:
        return None
    return (target - entry) / (entry - stop)


def earnings_soon(days_to_earnings) -> bool:
    return days_to_earnings is not None and 0 <= days_to_earnings <= TRADE["earnings_window_days"]


def position_size(f, signal: str, entry, stop, regime: str, days_to_earnings,
                  portfolio_value: float | None = None, risk_pct: float | None = None) -> dict:
    pv = portfolio_value or TRADE["portfolio_value"]
    rp = risk_pct if risk_pct is not None else TRADE["risk_per_trade"]
    cap = TRADE["max_position"].get(signal, 0.0)
    out = {"shares": 0, "value": 0.0, "pct": 0.0, "risk_budget": pv * rp, "cap_pct": cap, "notes": []}
    if cap == 0 or not entry or not stop or entry <= stop:
        if cap == 0:
            out["notes"].append("no new position at this signal")
        return out
    volatile = (f.get("rvol_1y") or 0) > 0.45 or (f.get("beta") or 0) > 2.0
    if volatile:
        cap = min(cap, TRADE["volatile_max_position"])
        out["notes"].append("volatile stock: cap 5%")
    shares = min(math.floor(pv * rp / (entry - stop)), math.floor(cap * pv / entry))
    mult = TRADE["regime_size_mult"][regime]
    if mult < 1:
        out["notes"].append(f"{regime.lower()} market: size x{mult}")
    if earnings_soon(days_to_earnings):
        mult *= TRADE["earnings_size_mult"]
        out["notes"].append("earnings within ~2 trading days: 25% of normal size")
    shares = math.floor(shares * mult)
    out.update(shares=shares, value=shares * entry, pct=shares * entry / pv, cap_pct=cap)
    return out


def trailing_plan(entry, stop, atr) -> dict | None:
    if not (entry and stop) or entry <= stop:
        return None
    r = entry - stop
    return {
        "R": r,
        "plus_1R": {"price": entry + r, "action": "move stop to breakeven", "new_stop": entry},
        "plus_2R": {"price": entry + 2 * r, "action": "stop to entry + 0.5R", "new_stop": entry + 0.5 * r},
        "plus_3R": {"price": entry + 3 * r, "action": f"trail at highest close - 2xATR ({2 * (atr or 0):.2f})"},
        "tranches": [0.25, 0.25, 0.25, 0.25],
    }
