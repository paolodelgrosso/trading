"""Backtest trend su BTC ed ETH (giornaliero, 365 giorni l'anno, quote frazionarie).

Regola: long se la chiusura (00:00 UTC) è sopra la SMA di N giorni, altrimenti in cassa
(cassa a rendimento zero). Segnale alla chiusura di t, ordine all'apertura di t+1.
ETH ha anche la variante "C" (serve anche BTC sopra la propria SMA). Capitale 100.000,
investito per intero nello strumento: serve a misurare la regola, non a dimensionare il
rischio (un'allocazione reale sarebbe una frazione del patrimonio).

Fisco: plusvalenze 33% (redditi diversi, minusvalenze riportabili 4 anni), pagate alla
vendita; alla fine si liquida in via fittizia. Costi: percentuale per lato (griglia).
Griglia fissata prima di vedere i risultati: SMA 150/200/250, costi 0,1/0,5/1,0%.
"""
import sys

import numpy as np
import pandas as pd

import backtest as bt
from dati_crypto import bitstamp, yahoo
from db import ROOT

CAPITALE = 100_000.0
ALIQUOTA_CRYPTO = 0.33
SMA = (150, 200, 250)
COSTI = (0.001, 0.005, 0.01)

bt.ALIQUOTA = ALIQUOTA_CRYPTO  # la classe Fisco legge l'aliquota dal modulo


def segnale(close: pd.Series, n: int) -> pd.Series:
    sma = close.rolling(n).mean()
    return (close > sma) & sma.notna()


def simula(df: pd.DataFrame, sig: pd.Series, costo: float, capitale: float = CAPITALE):
    """df: open/close; sig: booleana alla chiusura di ogni giorno (decisione per domani)."""
    fisco = bt.Fisco()
    cash, unita, base, acquisti = capitale, 0.0, 0.0, 0
    equity, investito = [], []
    segnali = sig.reindex(df.index).fillna(False).to_numpy()
    aper, chiu = df["open"].to_numpy(), df["close"].to_numpy()
    for i, d in enumerate(df.index):
        if i > 0:
            vuole = segnali[i - 1]
            if vuole and unita == 0:
                unita = cash * (1 - costo) / aper[i]
                base, cash, acquisti = cash, 0.0, acquisti + 1
            elif not vuole and unita > 0:
                ricavo = unita * aper[i] * (1 - costo)
                cash += ricavo - fisco.imposta(d.year, ricavo - base)
                unita = 0.0
        equity.append(cash + unita * chiu[i])
        investito.append(unita * chiu[i] / equity[-1])
    finale = cash
    if unita > 0:
        ricavo = unita * chiu[-1] * (1 - costo)
        lat = max(0.0, ricavo - base - fisco.credito_residuo(df.index[-1].year))
        finale = ricavo - lat * ALIQUOTA_CRYPTO
    return pd.Series(equity, index=df.index), finale, acquisti, float(np.mean(investito))


def buy_and_hold(df: pd.DataFrame, costo: float, capitale: float = CAPITALE):
    unita = capitale * (1 - costo) / df["open"].iloc[0]
    eq = unita * df["close"]
    ricavo = eq.iloc[-1] * (1 - costo)
    return eq, ricavo - max(0.0, ricavo - capitale) * ALIQUOTA_CRYPTO


def metriche(eq: pd.Series, finale: float, capitale: float = CAPITALE) -> dict:
    anni = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"cagr": (finale / capitale) ** (1 / anni) - 1,
            "dd": (eq / eq.cummax() - 1).min(),
            "sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan}


def riga(etichetta, esegui):
    """esegui(costo) -> (equity, finale, acquisti, esposizione). Riga con CAGR ai 3 costi."""
    out, extra = {"variante": etichetta}, None
    for c in COSTI:
        eq, fin, n, esp = esegui(c)
        m = metriche(eq, fin)
        out[f"CAGR@{c:.1%}"] = m["cagr"]
        if c == COSTI[1]:
            extra = (m, n, esp)
    m, n, esp = extra
    out.update({"DD@0.5%": m["dd"], "Sharpe@0.5%": m["sharpe"], "Acquisti": n, "Esposiz.": esp})
    return out


def analizza(nome: str, df: pd.DataFrame, sma_btc: dict | None, dal: str | None) -> pd.DataFrame:
    """sma_btc: {n: serie bool del filtro BTC} per la variante C su ETH."""
    righe = []
    # stessa finestra per tutte le varianti (la più lunga serve a SMA250) e per il buy & hold
    inizio = max(df.index[max(SMA)], pd.Timestamp(dal or df.index[0]))
    d = df.loc[inizio:]
    for n in SMA:
        sig = segnale(df["close"], n)
        righe.append(riga(f"A  SMA{n}", lambda c, sig=sig: simula(d, sig.loc[inizio:], c)))
        if sma_btc is not None:
            sc = sig & sma_btc[n].reindex(sig.index).fillna(False)
            righe.append(riga(f"C  SMA{n} (+filtro BTC)", lambda c, sc=sc: simula(d, sc.loc[inizio:], c)))
    righe.append(riga("Buy & hold", lambda c: (*buy_and_hold(d, c), 1, 1.0)))
    t = pd.DataFrame(righe)
    print(f"\n=== {nome}  ({d.index[0]:%Y-%m-%d} -> {d.index[-1]:%Y-%m-%d}) ===")
    pd.options.display.float_format = "{:+.3f}".format
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))
    t.insert(0, "scenario", nome)
    return t


def main():
    btc = bitstamp("btcusd")
    eth = bitstamp("ethusd")
    out = []
    out.append(analizza("BTC USD, intero storico", btc, None, None))
    out.append(analizza("BTC USD, dal 2017-09 (come ETH)", btc, None, "2017-09-01"))
    filtro_btc = {n: segnale(btc["close"], n) for n in SMA}
    out.append(analizza("ETH USD (Bitstamp)", eth, filtro_btc, None))
    # controllo con i dati Yahoo in EUR
    out.append(analizza("BTC EUR (Yahoo, dal 2014)", yahoo("BTC-EUR"), None, None))
    out.append(analizza("ETH EUR (Yahoo)", yahoo("ETH-EUR"),
                        {n: segnale(yahoo("BTC-EUR")["close"], n) for n in SMA}, None))
    res = ROOT / "risultati"
    res.mkdir(exist_ok=True)
    pd.concat(out).to_csv(res / "crypto_robustezza.csv", index=False, float_format="%.4f")


if __name__ == "__main__":
    sys.exit(main())
