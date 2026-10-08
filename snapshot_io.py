"""Load/save feature snapshots (compact keys/rows format or full dict)."""
import json
from pathlib import Path


def load_snapshot(path) -> dict:
    d = json.loads(Path(path).read_text())
    if "rows" in d:  # compact format produced by the browser collector
        results = {r[0]: dict(zip(d["keys"], r)) for r in d["rows"]}
        return {"asof": d.get("asof"), "results": results, "breadth": d.get("breadth"),
                "benchmark": d.get("benchmark", "SPY"), "errors": d.get("errors", {})}
    return d


def save_snapshot(data: dict, path) -> None:
    Path(path).write_text(json.dumps(data, default=str))
