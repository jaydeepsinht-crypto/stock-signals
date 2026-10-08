"""Model configuration: weights, thresholds and trading parameters (Blueprint v1.0)."""

MODEL_VERSION = "v1.0"

# Category weights (sum = 1.0)
CATEGORY_WEIGHTS = {
    "quality": 0.25,
    "growth": 0.20,
    "valuation": 0.20,
    "technical": 0.20,
    "risk": 0.15,
}

# Factor weights in points (sum = 100). Category score = weighted average of its
# available factors; missing / N/A factors have their weight redistributed.
FACTOR_WEIGHTS = {
    "quality": {
        "roic": 4, "roe": 3, "gross_margin": 3, "oper_margin": 3,
        "fcf_margin": 4, "debt_ebitda": 4, "fcf_conversion": 4,
    },
    "growth": {
        "revenue_growth": 4, "eps_growth": 5, "fcf_growth": 4,
        "fwd_eps_growth": 3, "eps_revision": 4,
    },
    "valuation": {
        "fwd_pe": 4, "ev_ebitda": 4, "peg": 4, "p_fcf": 4, "dcf_mos": 4,
    },
    "technical": {
        "vs_200dma": 4, "vs_50dma": 3, "rel_strength": 4, "rsi": 3,
        "macd_trend": 3, "volume": 3,
    },
    "risk": {
        "volatility": 5, "max_drawdown": 5, "market_regime": 5,
    },
}

FACTOR_LABELS = {
    "roic": "ROIC", "roe": "ROE", "gross_margin": "Gross margin", "oper_margin": "Operating margin",
    "fcf_margin": "FCF margin", "debt_ebitda": "Net debt / EBITDA", "fcf_conversion": "FCF conversion",
    "revenue_growth": "Revenue growth (3Y)", "eps_growth": "EPS growth (3Y)", "fcf_growth": "FCF growth (3Y)",
    "fwd_eps_growth": "Forward EPS growth", "eps_revision": "Earnings revision (proxy)",
    "fwd_pe": "Forward P/E vs fair", "ev_ebitda": "EV/EBITDA vs fair", "peg": "PEG", "p_fcf": "Price / FCF",
    "dcf_mos": "DCF margin of safety",
    "vs_200dma": "Price vs 200-DMA", "vs_50dma": "Price vs 50-DMA", "rel_strength": "Relative strength vs S&P 500",
    "rsi": "RSI (14)", "macd_trend": "MACD / trend", "volume": "Volume confirmation",
    "volatility": "Beta / volatility", "max_drawdown": "Max drawdown (1Y)", "market_regime": "Market regime",
}

# Score -> label bands (Section 1)
SCORE_BANDS = [
    (85, "STRONG BUY"), (75, "BUY"), (65, "BUY / ACCUMULATE"), (55, "HOLD"),
    (45, "WEAK HOLD"), (30, "SELL / REDUCE"), (0, "STRONG SELL"),
]

# Valuation model parameters
VAL = {
    "growth_cap": 30.0,          # % growth used for valuation is capped here
    "dcf_growth_cap": 15.0,      # % FCF growth in DCF years 1-5 (after haircut)
    "dcf_growth_haircut": 0.8,   # consensus long-term growth is optimistic: use 80 %
    "terminal_growth": 0.03,
    "risk_free": 0.045,
    "equity_risk_premium": 0.055,
    "wacc_min": 0.085,
    "wacc_max": 0.12,
    "component_clip": (0.33, 3.0),  # each per-share value winsorised to [0.33x, 3x] price
    "fv_weights": {"dcf": 0.40, "pe": 0.25, "ev_ebitda": 0.15, "p_fcf": 0.10, "peg": 0.10,
                   "pb": 0.40},   # pb (justified P/B) replaces DCF for banks/insurers
    "target_weights": {"dcf": 0.40, "pe": 0.30, "ev_ebitda": 0.20, "analyst": 0.10},
    "target_cap": 1.5,           # x fair value
    "target_cap_high_growth": 2.0,
}

# Trading parameters
TRADE = {
    "portfolio_value": 100_000.0,
    "risk_per_trade": 0.0075,     # 0.75 %
    "max_stop_distance": 0.15,    # stop never more than 15 % below entry
    "entry_zone": 0.03,
    "max_position": {"STRONG BUY": 0.10, "BUY": 0.075, "BUY / ACCUMULATE": 0.05},
    "volatile_max_position": 0.05,
    "regime_size_mult": {"BULL": 1.0, "NEUTRAL": 0.8, "BEAR": 0.5},
    "earnings_size_mult": 0.25,
    "earnings_window_days": 4,    # ~2 trading days, calendar approximation
}

# Industries where FCF / EBITDA based factors are not meaningful
FINANCIAL_INDUSTRY_KEYWORDS = ("bank", "insurance", "capital markets", "mortgage", "reit")

# Minimum peer-group size for sector-relative percentiles
MIN_PEER_GROUP = 5
