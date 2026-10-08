"""Persistence (Blueprint s.59, s.79). SQLite by default; schema mirrors schema_postgres.sql.

Every signal row stores model_version, data timestamp, signal timestamp, score, factor scores,
entry, target and stop so future model versions can be compared against past signals.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT UNIQUE NOT NULL, name TEXT, sector TEXT,
    industry TEXT, exchange TEXT, market_cap REAL, active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS features (
    company_id INTEGER, date TEXT, data_timestamp TEXT, payload TEXT,
    PRIMARY KEY (company_id, date));
CREATE TABLE IF NOT EXISTS scores (
    company_id INTEGER, date TEXT, model_version TEXT, quality_score REAL, growth_score REAL,
    valuation_score REAL, technical_score REAL, risk_score REAL, total_score REAL,
    factor_scores TEXT, PRIMARY KEY (company_id, date, model_version));
CREATE TABLE IF NOT EXISTS signals (
    company_id INTEGER, date TEXT, model_version TEXT, data_timestamp TEXT, signal_timestamp TEXT,
    signal TEXT, price REAL, fair_value REAL, entry_price REAL, entry_low REAL, entry_high REAL,
    target_price REAL, stop_loss REAL, risk_reward REAL, position_size INTEGER, confidence REAL,
    market_regime TEXT, PRIMARY KEY (company_id, date, model_version));
"""


class Store:
    def __init__(self, path: str | Path = "stock_signal.db"):
        self.con = sqlite3.connect(str(path))
        self.con.executescript(SCHEMA)

    def _company_id(self, s: dict) -> int:
        cur = self.con.execute(
            "INSERT INTO companies(ticker,name,sector,industry,market_cap) VALUES(?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, sector=excluded.sector, "
            "industry=excluded.industry, market_cap=excluded.market_cap, updated_at=CURRENT_TIMESTAMP "
            "RETURNING id",
            (s["ticker"], s.get("name"), s.get("sector"), s.get("industry"), s["metrics"].get("market_cap")))
        return cur.fetchone()[0]

    def save_run(self, analysis: dict, features: dict | None = None) -> int:
        n = 0
        asof = analysis.get("asof")
        for s in analysis["stocks"]:
            cid = self._company_id(s)
            d, mv, t, c = s["signal_date"], s["model_version"], s["trade"], s["categories"]
            if features and s["ticker"] in features:
                self.con.execute("INSERT OR REPLACE INTO features VALUES(?,?,?,?)",
                                 (cid, d, asof, json.dumps(features[s["ticker"]])))
            self.con.execute("INSERT OR REPLACE INTO scores VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (cid, d, mv, c["quality"], c["growth"], c["valuation"], c["technical"],
                              c["risk"], s["score"],
                              json.dumps({k: v["score"] for k, v in s["factors"].items()})))
            self.con.execute("INSERT OR REPLACE INTO signals VALUES(?,?,?,?,datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?)",
                             (cid, d, mv, asof, s["signal"], s["price"], s["valuation"]["fair_value"],
                              t.get("entry"), t.get("zone_low"), t.get("zone_high"), t.get("target"),
                              t.get("stop"), t.get("risk_reward"), t["position"]["shares"], s["confidence"],
                              analysis["regime"]["regime"]))
            n += 1
        self.con.commit()
        return n

    def history(self, ticker: str):
        return self.con.execute(
            "SELECT s.date, s.model_version, s.signal, s.price, sc.total_score, s.entry_price, s.target_price, "
            "s.stop_loss FROM signals s JOIN companies c ON c.id=s.company_id "
            "JOIN scores sc ON sc.company_id=s.company_id AND sc.date=s.date AND sc.model_version=s.model_version "
            "WHERE c.ticker=? ORDER BY s.date", (ticker,)).fetchall()
