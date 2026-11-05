"""Offline tests for market.py (home page heat map and index list)."""
import json
import os
import time

import pytest

import market


def test_yahoo_symbols():
    assert market.yahoo_symbol("BRK.B") == "BRK-B"
    assert market.yahoo_symbol(" aapl ") == "AAPL"


def test_heatmap_groups_by_sector_and_weights_by_size(monkeypatch):
    monkeypatch.setattr(market, "members", lambda idx: [
        {"symbol": "AAA", "name": "A Co", "sector": "Tech"},
        {"symbol": "BBB", "name": "B Co", "sector": "Tech"},
        {"symbol": "CCC", "name": "C Co", "sector": "Energy"},
        {"symbol": "ZZZ", "name": "Delisted", "sector": "Energy"},
    ])
    market._mem.clear()
    monkeypatch.setattr(market, "_universe", lambda: {
        "AAA": {"cap": 300.0, "change": 2.0, "price": 10},     # screener gives percent
        "BBB": {"cap": 100.0, "change": -2.0, "price": 5},
        "CCC": {"cap": 50.0, "change": None, "price": 1},
    })
    d = market.heatmap("sp500")
    tech, energy = d["sectors"]                                  # biggest sector first
    assert tech["name"] == "Tech" and [i["symbol"] for i in tech["items"]] == ["AAA", "BBB"]
    assert tech["items"][0]["change"] == pytest.approx(0.02)
    assert tech["change"] == pytest.approx((300 * 0.02 + 100 * -0.02) / 400)   # cap-weighted
    assert energy["change"] is None                               # no move data at all
    assert d["missing"] == ["ZZZ"]


def test_unknown_index():
    with pytest.raises(KeyError):
        market.heatmap("ftse")


def test_members_fall_back_to_stale_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE", tmp_path)
    stale = [{"symbol": f"S{i}", "name": "x", "sector": "y"} for i in range(30)]
    path = tmp_path / "members_dow.json"
    path.write_text(json.dumps(stale), encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))

    def down():
        raise OSError("source unavailable")
    monkeypatch.setitem(market.SOURCES, "dow", down)
    assert market.members("dow") == stale
