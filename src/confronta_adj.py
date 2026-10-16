"""Confronta la chiusura_adj calcolata in casa con quella di Yahoo (dal 2002)."""
import numpy as np
import pandas as pd

from adjust import carica_prezzi

df = carica_prezzi()
righe = []
for t, g in df.groupby("ticker"):
    mia, yh, raw = g["chiusura_adj"], g["chiusura_adj_yahoo"], g["chiusura"]
    # l'ultimo giorno entrambe coincidono con chiusura: il rapporto misura la deriva
    rapp = np.log(mia / yh)
    r_mia, r_yh = mia.pct_change(), yh.pct_change()
    righe.append(dict(
        ticker=t,
        TR_mio=mia.iloc[-1] / mia.iloc[0] - 1,
        TR_yahoo=yh.iloc[-1] / yh.iloc[0] - 1,
        TR_prezzo=raw.iloc[-1] / raw.iloc[0] - 1,
        dev_max_log=rapp.abs().max(),
        max_diff_rend_gg=(r_mia - r_yh).abs().max(),
        n_ritorni_diversi_1bp=int(((r_mia - r_yh).abs() > 1e-4).sum()),
    ))
o = pd.DataFrame(righe)
pd.set_option("display.width", 200)
pd.options.display.float_format = "{:.4f}".format
print(o.to_string(index=False))
print("\nTicker con deviazione log massima > 1%:")
print(o[o.dev_max_log > 0.01][["ticker", "TR_mio", "TR_yahoo", "dev_max_log"]].to_string(index=False))
