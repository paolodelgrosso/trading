"""Sensibilità delle strategie C e M ai parametri, senza scegliere il migliore.

uso: robustezza.py italia|usa
Griglia fissata in anticipo: C con SMA 150/200/250, M con calo a 3 gg di 4/5/6%.
In Italia anche A e C senza le banche (e il buy & hold senza banche).
Tutte le metriche partono dallo stesso giorno (primo con SMA200): le varianti con
SMA250 restano ferme in cassa per ~50 giorni in più.
"""
import sys

import pandas as pd

import backtest as bt
from db import ROOT
from scarica_prezzi import leggi_universo

BANCHE = ["ISP.MI", "UCG.MI", "BAMI.MI", "BPE.MI", "BMPS.MI", "FBK.MI", "MB.MI", "BGN.MI"]


def main(mercato: str) -> None:
    bt.UNIVERSO_TICKER = list(leggi_universo(ROOT / bt.UNIVERSI[mercato]))
    bt.BENCHMARK = bt.UNIVERSO_TICKER[0]
    adj, chiu, aper, adj_netto, div = bt.matrici()
    azioni = [t for t in adj.columns if t != bt.BENCHMARK]
    inizio = adj[azioni].rolling(200).mean().dropna(how="all").index[0]
    righe = []

    def corri(etichetta, nome, params, escludi=()):
        cols = [c for c in adj.columns if c not in escludi]
        bt.PARAMETRI.update({"sma": 200, "calo": 0.05})
        bt.PARAMETRI.update(params)
        r = bt.simula(nome, adj[cols], chiu[cols], aper[cols], div)
        eq = r["equity"].loc[inizio:]
        m = bt.metriche(eq, r["finale_netto"])
        righe.append({"variante": etichetta, "CAGR netto": m["CAGR netto"],
                      "Max drawdown": m["Max drawdown"], "Sharpe": m["Sharpe (rf=0)"],
                      "Esposizione": r["esposizione"].loc[inizio:].mean(),
                      "Operazioni": len(r["operazioni"])})
        print(f"  {etichetta:38} CAGR {m['CAGR netto']:+.1%}  DD {m['Max drawdown']:.0%}  "
              f"Sharpe {m['Sharpe (rf=0)']:.2f}", flush=True)

    print(f"== {mercato} ==")
    for sma in (150, 200, 250):
        corri(f"C  SMA{sma}", "C", {"sma": sma})
    for calo in (0.04, 0.05, 0.06):
        corri(f"M  calo {calo:.0%}", "M", {"calo": calo})
    if mercato == "italia":
        corri("A  SMA200 senza banche", "A", {}, BANCHE)
        corri("C  SMA200 senza banche", "C", {}, BANCHE)
        cols = [t for t in azioni if t not in BANCHE]
        eq, fin, n = bt.buy_and_hold(adj_netto[cols + [bt.BENCHMARK]].loc[inizio:], chiu, inizio)
        m = bt.metriche(eq, fin)
        righe.append({"variante": f"B&H senza banche ({n} titoli)", "CAGR netto": m["CAGR netto"],
                      "Max drawdown": m["Max drawdown"], "Sharpe": m["Sharpe (rf=0)"]})
        print(f"  {'B&H senza banche':38} CAGR {m['CAGR netto']:+.1%}  DD {m['Max drawdown']:.0%}  "
              f"Sharpe {m['Sharpe (rf=0)']:.2f}")
    out = ROOT / "risultati"
    out.mkdir(exist_ok=True)
    pd.DataFrame(righe).to_csv(out / f"robustezza_{mercato}.csv", index=False, float_format="%.4f")


if __name__ == "__main__":
    main(sys.argv[1])
