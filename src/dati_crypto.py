"""Storico giornaliero BTC/ETH: Bitstamp (API pubblica, USD, lungo) e Yahoo (EUR, controllo).

Le candele sono UTC (00:00-24:00). L'ultima candela, ancora in formazione, viene scartata.
Cache in data/crypto/*.csv (data/ è fuori da git).
"""
import json
import time
import urllib.request

import pandas as pd
import yfinance as yf

from db import ROOT

CACHE = ROOT / "data" / "crypto"


def _oggi_utc() -> pd.Timestamp:
    return pd.Timestamp.now("UTC").tz_localize(None).normalize()


def bitstamp(pair: str, rinnova: bool = False) -> pd.DataFrame:
    f = CACHE / f"bitstamp_{pair}.csv"
    if f.exists() and not rinnova:
        return pd.read_csv(f, index_col=0, parse_dates=True)
    righe, start = [], int(pd.Timestamp("2011-01-01").timestamp())
    fine = int(pd.Timestamp.now("UTC").timestamp())
    while start < fine:
        url = f"https://www.bitstamp.net/api/v2/ohlc/{pair}/?step=86400&limit=1000&start={start}"
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)["data"]["ohlc"]
        # a ogni richiesta può tornare meno di 1000 candele (o nessuna prima della quotazione)
        start = int(d[-1]["timestamp"]) + 86400 if d else start + 1000 * 86400
        righe += d
        time.sleep(0.5)
    df = pd.DataFrame(righe).astype({"open": float, "high": float, "low": float,
                                      "close": float, "volume": float, "timestamp": int})
    df.index = pd.to_datetime(df["timestamp"], unit="s").dt.normalize()
    df = df[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df.index < _oggi_utc()]
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(f)
    return df


def yahoo(simbolo: str, rinnova: bool = False) -> pd.DataFrame:
    f = CACHE / f"yahoo_{simbolo}.csv"
    if f.exists() and not rinnova:
        return pd.read_csv(f, index_col=0, parse_dates=True)
    d = yf.download(simbolo, period="max", interval="1d", auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    idx = pd.to_datetime(d.index)
    d.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d[d.index < _oggi_utc()]
    CACHE.mkdir(parents=True, exist_ok=True)
    d.to_csv(f)
    return d


if __name__ == "__main__":
    for p in ("btcusd", "ethusd"):
        d = bitstamp(p, rinnova=True)
        print(f"bitstamp {p}: {len(d)} giorni {d.index[0]:%Y-%m-%d} -> {d.index[-1]:%Y-%m-%d}")
    for s in ("BTC-USD", "BTC-EUR", "ETH-USD", "ETH-EUR"):
        d = yahoo(s, rinnova=True)
        print(f"yahoo {s}: {len(d)} giorni {d.index[0]:%Y-%m-%d} -> {d.index[-1]:%Y-%m-%d}")
    # controllo di coerenza Bitstamp vs Yahoo (rendimenti giornalieri sulla stessa valuta)
    for b, y in (("btcusd", "BTC-USD"), ("ethusd", "ETH-USD")):
        rb = bitstamp(b)["close"].pct_change()
        ry = yahoo(y)["close"].pct_change()
        j = pd.concat([rb, ry], axis=1, join="inner").dropna()
        print(f"{b} vs {y}: {len(j)} giorni in comune, correlazione rendimenti {j.corr().iloc[0, 1]:.3f}, "
              f"scarto medio assoluto {(j.iloc[:, 0] - j.iloc[:, 1]).abs().mean():.4f}")
