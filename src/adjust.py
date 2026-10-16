"""Chiusura rettificata calcolata in casa da `chiusura` e `dividendi`.

Yahoo applica dividendi in lire a prezzi in euro prima del 2002 e produce
chiusura_adj negative: la ricalcoliamo noi e leggiamo solo dal 2002.
`chiusura` di Yahoo è già corretta per gli split, e così i dividendi.
"""
import pandas as pd

from db import DATA_INIZIO_BACKTEST, connetti


def calcola_adj(chiusura: pd.Series, dividendi: pd.Series, aliquota: float = 0.0) -> pd.Series:
    """chiusura: indice data (str, ordinato). dividendi: indice data_stacco (str).

    Fattore allo stacco = 1 - importo*(1-aliquota) / chiusura del giorno prima;
    adj = chiusura * prodotto dei fattori degli stacchi successivi alla data.
    aliquota=0.26 dà il total return netto per il regime amministrato.
    """
    fattori = pd.Series(1.0, index=chiusura.index)
    pos = chiusura.index.searchsorted(dividendi.index.to_numpy())  # primo giorno >= stacco
    for p, importo in zip(pos, dividendi.to_numpy()):
        if p == 0:  # stacco prima del primo prezzo: non ci serve
            continue
        fattori.iloc[p] *= 1.0 - importo * (1.0 - aliquota) / chiusura.iloc[p - 1]
    # prodotto dei fattori con indice strettamente maggiore
    cum = fattori[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    return chiusura * cum


def carica_prezzi(conn=None, tickers: list[str] | None = None, aliquota_div: float = 0.0,
                  dal: str = DATA_INIZIO_BACKTEST) -> pd.DataFrame:
    """Prezzi dal 2002 con `chiusura_adj` ricalcolata (sostituisce quella di Yahoo,
    tenuta in `chiusura_adj_yahoo`)."""
    chiuso = conn is None
    conn = conn or connetti()
    prezzi = pd.read_sql("SELECT * FROM prezzi_giornalieri ORDER BY ticker, data", conn)
    div = pd.read_sql("SELECT * FROM dividendi", conn)
    if chiuso:
        conn.close()
    if tickers:
        prezzi = prezzi[prezzi.ticker.isin(tickers)]

    pezzi = []
    for t, g in prezzi.groupby("ticker"):
        g = g.set_index("data")
        d = div[div.ticker == t].set_index("data_stacco")["importo"]
        g = g.rename(columns={"chiusura_adj": "chiusura_adj_yahoo"})
        g["chiusura_adj"] = calcola_adj(g["chiusura"], d, aliquota_div)
        g = g[g.index >= dal]
        pezzi.append(g.reset_index())
    return pd.concat(pezzi, ignore_index=True)
