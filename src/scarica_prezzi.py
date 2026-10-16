import csv
import sys
import time
from datetime import datetime

import pandas as pd
import yfinance as yf

from db import ROOT, connetti

UNIVERSO = ROOT / "universo.csv"
PAUSA_SECONDI = 1.0
ORA_CHIUSURA_DATI = 18  # prima di quest'ora la barra di oggi è parziale

COLONNE = {
    "Open": "apertura",
    "High": "massimo",
    "Low": "minimo",
    "Close": "chiusura",
    "Adj Close": "chiusura_adj",
    "Volume": "volume",
}


def leggi_universo() -> dict[str, str]:
    """ticker -> div_valuta; la colonna è opzionale (vuota = dividendi già in EUR)."""
    with open(UNIVERSO, newline="", encoding="utf-8-sig") as f:
        return {
            r["ticker"].strip(): (r.get("div_valuta") or "").strip()
            for r in csv.DictReader(f) if r["ticker"].strip()
        }


def scarica(ticker: str) -> pd.DataFrame:
    df = yf.download(
        ticker, period="max", interval="1d",
        auto_adjust=False, actions=False, progress=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    mancanti = set(COLONNE) - set(df.columns)
    if mancanti:
        raise ValueError(f"colonne mancanti: {sorted(mancanti)}")

    df = df[list(COLONNE)].rename(columns=COLONNE)
    df = df.dropna(subset=["chiusura"])

    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.strftime("%Y-%m-%d")
    df = df[~df.index.duplicated(keep="last")]

    # Prima delle 18 la barra di oggi è parziale (borsa chiusa alle 17:30): scartala.
    adesso = datetime.now()
    if adesso.hour < ORA_CHIUSURA_DATI:
        df = df[df.index != adesso.strftime("%Y-%m-%d")]

    df["volume"] = df["volume"].fillna(0).astype("int64")
    return df


def _dividendi_yahoo(ticker: str) -> pd.Series:
    div = yf.Ticker(ticker).dividends
    if div is None or div.empty:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime(div.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    div = pd.Series(div.to_numpy(dtype="float64"), index=idx.normalize())
    div = div[~div.index.duplicated(keep="last")]
    return div[div > 0]


_EURUSD: pd.Series | None = None


def _eurusd() -> pd.Series:
    """USD per 1 EUR, chiusura giornaliera (cache di processo)."""
    global _EURUSD
    if _EURUSD is None:
        fx = yf.download("EURUSD=X", period="max", interval="1d",
                         auto_adjust=False, progress=False)
        if isinstance(fx.columns, pd.MultiIndex):
            fx.columns = fx.columns.get_level_values(0)
        s = fx["Close"].dropna()
        idx = pd.to_datetime(s.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        _EURUSD = pd.Series(s.to_numpy(dtype="float64"), index=idx.normalize())
        if _EURUSD.empty:
            raise ValueError("EURUSD=X: nessun dato")
    return _EURUSD


def _dividendi_usd_in_eur(div_milano: pd.Series) -> pd.Series:
    """yfinance converte i dividendi USD in EUR al cambio SPOT di oggi (importo*spot = cifre
    tonde in USD) e Yahoo usa il numero USD come fosse EUR: entrambi sbagliati.
    Qui si inverte la conversione con lo spot e si riconverte al cambio dello stacco."""
    if div_milano.empty:
        return div_milano
    fx = _eurusd()
    usd = div_milano * fx.iloc[-1]
    cambio = fx.reindex(fx.index.union(usd.index)).ffill().reindex(usd.index)
    # EURUSD=X su Yahoo parte a dicembre 2003: prima, usa il primo cambio disponibile.
    prima = cambio.isna()
    if prima.any():
        print(f"  ATTENZIONE: {int(prima.sum())} dividendi prima del {fx.index[0]:%Y-%m-%d} "
              f"convertiti al cambio {fx.iloc[0]:.3f} (storico non disponibile)")
        cambio = cambio.fillna(fx.iloc[0])
    return usd / cambio


def scarica_dividendi(ticker: str, div_valuta: str = "") -> pd.Series:
    div = _dividendi_yahoo(ticker)
    if div_valuta.upper() == "USD":
        div = _dividendi_usd_in_eur(div)
    elif div_valuta and div_valuta.upper() != "EUR":
        raise ValueError(f"valuta dividendi non gestita: {div_valuta}")
    div.index = div.index.strftime("%Y-%m-%d")
    return div


def salva(conn, ticker: str, df: pd.DataFrame, div: pd.Series) -> None:
    righe = [
        (ticker, data,
         float(r.apertura), float(r.massimo), float(r.minimo),
         float(r.chiusura), float(r.chiusura_adj), int(r.volume))
        for data, r in zip(df.index, df.itertuples(index=False))
    ]
    with conn:
        conn.execute("DELETE FROM prezzi_giornalieri WHERE ticker = ?", (ticker,))
        conn.executemany(
            "INSERT INTO prezzi_giornalieri VALUES (?, ?, ?, ?, ?, ?, ?, ?)", righe
        )
        conn.execute("DELETE FROM dividendi WHERE ticker = ?", (ticker,))
        conn.executemany(
            "INSERT INTO dividendi VALUES (?, ?, ?)",
            [(ticker, d, float(v)) for d, v in div.items()],
        )
        conn.execute(
            "INSERT OR REPLACE INTO aggiornamenti VALUES (?, ?, ?, ?, ?)",
            (ticker, datetime.now().isoformat(timespec="seconds"),
             len(righe), df.index[0], df.index[-1]),
        )


def main() -> None:
    config = leggi_universo()
    tickers = sys.argv[1:] or list(config)
    conn = connetti()
    errori = []
    n = len(tickers)

    for i, t in enumerate(tickers, 1):
        try:
            df = scarica(t)
            if df.empty:
                errori.append((t, "nessun dato"))
                print(f"[{i}/{n}] {t:12} NESSUN DATO")
            else:
                div = scarica_dividendi(t, config.get(t, ""))
                salva(conn, t, df, div)
                print(f"[{i}/{n}] {t:12} {len(df):6} righe  {df.index[0]} -> {df.index[-1]}  {len(div):3} dividendi")
        except Exception as e:
            errori.append((t, str(e)))
            print(f"[{i}/{n}] {t:12} ERRORE: {e}")
        time.sleep(PAUSA_SECONDI)

    conn.close()

    if errori:
        print(f"\n{len(errori)} ticker con problemi:")
        for t, msg in errori:
            print(f"  {t}: {msg}")
        sys.exit(1)
    print("\nCompletato senza errori.")


if __name__ == "__main__":
    main()
