"""Finviz data client.

Pulls, per ticker:
  * quote page snapshot table      https://finviz.com/quote.ashx?t=XXX
  * annual statements (JSON)       /api/statement.ashx?t=XXX&so=F&s=IA|CA|BA
  * daily OHLCV (JSON, ~10 years)  /api/quote.ashx?instrument=stock&ticker=XXX&timeframe=d
  * S&P 500 breadth from the screener (count above 200-DMA / total)

Notes
-----
* Finviz rate-limits aggressive clients. Requests are spaced (``delay``) and retried on 429.
* Responses are cached on disk per ticker per day (``cache_dir``) so reruns are cheap.
* Respect Finviz's terms of use. For heavy/commercial use, Finviz Elite offers an official export.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from features import build_features, snapshot_pairs

BASE = "https://finviz.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


class FinvizClient:
    def __init__(self, delay: float = 0.6, cache_dir: str | Path | None = "cache", retries: int = 4):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay
        self.retries = retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    # ------------------------------------------------------------------ http
    def _get(self, path: str) -> requests.Response:
        for attempt in range(self.retries):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            r = self.s.get(BASE + path, timeout=30)
            self._last = time.time()
            if r.status_code == 429:
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"Finviz rate limit persisted for {path}")

    def _cached(self, key: str, fn):
        if not self.cache_dir:
            return fn()
        p = self.cache_dir / f"{dt.date.today():%Y%m%d}_{key}.json"
        if p.exists():
            return json.loads(p.read_text())
        val = fn()
        p.write_text(json.dumps(val))
        return val

    # ------------------------------------------------------------- endpoints
    def snapshot(self, ticker: str) -> dict:
        def fetch():
            soup = BeautifulSoup(self._get(f"/quote.ashx?t={ticker}&p=d").text, "html.parser")
            cells = [td.get_text(strip=True) for td in soup.select("table.snapshot-table2 td")]
            links = [a.get_text(strip=True) for a in
                     soup.select('a[href*="f=sec_"], a[href*="f=ind_"], a[href*="f=geo_"]')]
            title = (soup.title.get_text() if soup.title else "").split(" - ", 1)
            name = title[1].replace(" Stock Price and Quote", "") if len(title) > 1 else ticker
            return {"kv": snapshot_pairs(cells), "name": name,
                    "sector": links[0] if links else None,
                    "industry": links[1] if len(links) > 1 else None,
                    "country": links[2] if len(links) > 2 else None}
        return self._cached(f"{ticker}_snap", fetch)

    def statement(self, ticker: str, kind: str) -> dict:
        """kind: IA (income annual), CA (cash flow annual), BA (balance annual)."""
        def fetch():
            try:
                return self._get(f"/api/statement.ashx?t={ticker}&so=F&s={kind}").json().get("data", {})
            except Exception:
                return {}
        return self._cached(f"{ticker}_{kind}", fetch)

    def ohlc(self, ticker: str) -> dict:
        def fetch():
            j = self._get(f"/api/quote.ashx?instrument=stock&ticker={ticker}"
                          f"&timeframe=d&type=new&events=false").json()
            return {k: j[k] for k in ("date", "open", "high", "low", "close", "volume")}
        return self._cached(f"{ticker}_ohlc", fetch)

    def breadth_sp500(self) -> dict:
        def count(f):
            m = re.search(r"(\d[\d,]*)\s*Total", self._get(f"/screener.ashx?v=111&f={f}").text)
            return int(m.group(1).replace(",", "")) if m else None
        def fetch():
            total, above = count("idx_sp500"), count("idx_sp500,ta_sma200_pa")
            pct = above / total * 100 if total and above is not None else None
            return {"sp500_total": total, "above_sma200": above, "pct": pct}
        return self._cached("breadth", fetch)

    # -------------------------------------------------------------- features
    def features(self, ticker: str) -> dict:
        t = ticker.upper().replace(".", "-")
        return build_features(t, self.snapshot(t), self.statement(t, "IA"), self.statement(t, "CA"),
                              self.statement(t, "BA"), self.ohlc(t))

    def universe_features(self, tickers: list[str], benchmark: str = "SPY", progress=None) -> dict:
        out, errors = {}, {}
        all_t = [benchmark] + [t for t in tickers if t.upper() != benchmark]
        for i, t in enumerate(all_t):
            try:
                out[t.upper()] = self.features(t)
            except Exception as e:  # keep going; report failures
                errors[t.upper()] = str(e)
                if i == 0:  # benchmark failed: Finviz is unreachable/blocking, stop instead of 40 slow failures
                    raise RuntimeError(f"cannot reach Finviz ({e})") from e
            if progress:
                progress(i + 1, len(all_t), t)
        try:
            breadth = self.breadth_sp500()
        except Exception as e:
            breadth, errors["breadth"] = {}, str(e)
        return {"asof": dt.datetime.now().isoformat(timespec="seconds"), "results": out,
                "breadth": breadth, "errors": errors, "benchmark": benchmark}
