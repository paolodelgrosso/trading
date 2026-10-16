"""Backtest strategie trend su azioni FTSE MIB (universo = composizione ATTUALE: bias di
sopravvivenza, i risultati sono ottimistici).

Regole (parametri canonici fissi, nessuna ottimizzazione):
  A  tieni il titolo finché chiude sopra la media a 200 gg
  B  entra alla rottura del massimo a 50 gg, esci sotto il minimo a 20 gg
Esecuzione: segnale sulla chiusura di t, ordine eseguito all'APERTURA di t+1 (approssima
l'ordine limite del mattino: nessun mancato fill modellato).
Vincoli: 10% dell'equity per posizione all'ingresso, niente leva, 5 EUR a ordine.
Fisco (amministrato): plusvalenze azioni 26% con minusvalenze compensabili nei 4 anni
successivi; dividendi netti 26% (reddito di capitale, non compensabile).
"""
import sys

import numpy as np
import pandas as pd

from adjust import carica_prezzi
from db import ROOT, connetti
from scarica_prezzi import leggi_universo

CAPITALE = 100_000.0
PESO = 0.10
PESO_MAX = 0.15       # oltre questo peso a fine giornata si riduce a PESO il giorno dopo
MIN_FRAZIONE = 0.5    # niente posizioni sotto metà del peso target (evita acquisti da pochi euro)
COMMISSIONE = 5.0
ALIQUOTA = 0.26
UNIVERSI = {"italia": "universo.csv", "usa": "universo_usa.csv"}
BENCHMARK = "FTSEMIB.MI"  # impostato in main(): è la prima riga del CSV dell'universo
UNIVERSO_TICKER: list[str] = []
MOMENTUM_GG = 126  # solo per ordinare i segnali concorrenti, non filtra nulla


def matrici():
    segnali = carica_prezzi(tickers=UNIVERSO_TICKER, aliquota_div=0.0)     # adj lorda: per i segnali
    netto = carica_prezzi(tickers=UNIVERSO_TICKER, aliquota_div=ALIQUOTA)  # adj con dividendi netti: per buy & hold
    def pivot(d, c):
        m = d.pivot(index="data", columns="ticker", values=c).sort_index()
        m.index = pd.to_datetime(m.index)
        return m
    adj = pivot(segnali, "chiusura_adj")
    chiu = pivot(segnali, "chiusura")
    aper = pivot(segnali, "apertura")
    adj_netto = pivot(netto, "chiusura_adj")
    conn = connetti()
    div = pd.read_sql("SELECT * FROM dividendi", conn)
    conn.close()
    div["data_stacco"] = pd.to_datetime(div["data_stacco"])
    return adj, chiu, aper, adj_netto, div


def segnali_strategia(nome: str, adj: pd.DataFrame):
    """Ritorna (entra, esci) booleani per data/ticker, valutati sulla chiusura."""
    if nome == "A":
        sma = adj.rolling(200).mean()
        valido = sma.notna()
        sopra = (adj > sma) & valido
        return sopra, ~sopra & valido
    if nome == "B":
        mass = adj.rolling(50).max().shift(1)
        minimo = adj.rolling(20).min().shift(1)
        return (adj > mass) & mass.notna(), (adj < minimo) & minimo.notna()
    raise ValueError(nome)


class Fisco:
    """Minusvalenze azioni: riportabili negli anni Y..Y+4."""

    def __init__(self):
        self.perdite: list[list] = []  # [anno, importo]
        self.pagato = 0.0

    def imposta(self, anno: int, guadagno: float) -> float:
        if guadagno < 0:
            self.perdite.append([anno, -guadagno])
            return 0.0
        residuo = guadagno
        for p in self.perdite:
            if residuo <= 0:
                break
            if anno <= p[0] + 4 and p[1] > 0:
                uso = min(p[1], residuo)
                p[1] -= uso
                residuo -= uso
        tasse = residuo * ALIQUOTA
        self.pagato += tasse
        return tasse

    def credito_residuo(self, anno: int) -> float:
        return sum(p[1] for p in self.perdite if anno <= p[0] + 4)


def simula(nome, adj, chiu, aper, div):
    entra, esci = segnali_strategia(nome, adj)
    mom = adj.pct_change(MOMENTUM_GG)
    date = adj.index
    cols = np.array(adj.columns)
    ent_np, mom_np = entra.to_numpy(), mom.to_numpy()
    non_bench = cols != BENCHMARK
    div_per_data = {d: g.set_index("ticker")["importo"] for d, g in div.groupby("data_stacco")}

    cash = CAPITALE
    pos: dict[str, dict] = {}
    fisco = Fisco()
    tot_comm = tot_div = 0.0
    n_riduzioni = 0
    peso_max_visto = 0.0
    chiusure_ult = {}
    ordini_vendi: list[str] = []
    ordini_compra: list[str] = []
    ordini_riduci: list[str] = []
    equity_prec = CAPITALE
    equity, esposizione, operazioni = [], [], []

    for i, d in enumerate(date):
        # 1. dividendi su posizioni già in portafoglio (ingresso prima di oggi)
        if d in div_per_data:
            for t, imp in div_per_data[d].items():
                if t in pos:
                    incasso = pos[t]["azioni"] * imp * (1 - ALIQUOTA)
                    cash += incasso
                    tot_div += incasso
        # 2. ordini decisi ieri sera, eseguiti all'apertura
        for t in ordini_vendi:
            p = pos.pop(t)
            prezzo = aper.at[d, t]
            if np.isnan(prezzo):
                prezzo = chiusure_ult[t]
            ricavo = p["azioni"] * prezzo - COMMISSIONE
            guadagno = ricavo - p["costo"]
            tasse = fisco.imposta(d.year, guadagno)
            cash += ricavo - tasse
            tot_comm += COMMISSIONE
            operazioni.append(dict(ticker=t, entrata=p["data"], uscita=d, guadagno=guadagno,
                                   giorni=(d - p["data"]).days, rend=guadagno / p["costo"]))
        for t in ordini_riduci:
            if t not in pos:
                continue
            p = pos[t]
            prezzo = aper.at[d, t]
            if np.isnan(prezzo):
                continue
            n = int((p["azioni"] * prezzo - PESO * equity_prec) // prezzo)
            if n < 1 or n >= p["azioni"]:
                continue
            quota_costo = p["costo"] * n / p["azioni"]
            ricavo = n * prezzo - COMMISSIONE
            tasse = fisco.imposta(d.year, ricavo - quota_costo)
            cash += ricavo - tasse
            tot_comm += COMMISSIONE
            p["azioni"] -= n
            p["costo"] -= quota_costo
            n_riduzioni += 1
        for t in ordini_compra:
            prezzo = aper.at[d, t]
            if np.isnan(prezzo) or prezzo <= 0:
                continue
            budget = min(PESO * equity_prec, cash)
            if budget < MIN_FRAZIONE * PESO * equity_prec:
                continue
            azioni = int((budget - COMMISSIONE) // prezzo)
            if azioni < 1:
                continue
            costo = azioni * prezzo + COMMISSIONE
            cash -= costo
            tot_comm += COMMISSIONE
            pos[t] = dict(azioni=azioni, costo=costo, data=d)
        # 3. valorizzazione alla chiusura
        for t in list(pos):
            c = chiu.at[d, t]
            if not np.isnan(c):
                chiusure_ult[t] = c
        for t in pos:
            chiusure_ult.setdefault(t, aper.at[d, t])
        valore_pos = sum(p["azioni"] * chiusure_ult[t] for t, p in pos.items())
        eq = cash + valore_pos
        equity.append(eq)
        esposizione.append(valore_pos / eq)
        equity_prec = eq
        ordini_riduci = [t for t, p in pos.items() if p["azioni"] * chiusure_ult[t] > PESO_MAX * eq]
        peso_max_visto = max(peso_max_visto, max((p["azioni"] * chiusure_ult[t] / eq for t, p in pos.items()), default=0.0))
        # 4. segnali di stasera -> ordini di domani
        ordini_vendi = [t for t in pos if esci.at[d, t]]
        ok = np.flatnonzero(ent_np[i] & non_bench & ~np.isnan(mom_np[i]))
        ok = ok[np.argsort(-mom_np[i][ok])]
        ordini_compra = [cols[j] for j in ok if cols[j] not in pos]
        if i == len(date) - 1:
            break

    # liquidazione fittizia a fine periodo: imposta sulle plusvalenze latenti
    anno = date[-1].year
    latente = 0.0
    for t, p in pos.items():
        latente += p["azioni"] * chiusure_ult[t] - COMMISSIONE - p["costo"]
    # le latenti si compensano con le perdite residue e tra loro
    pos_g = sum(max(0, p["azioni"] * chiusure_ult[t] - COMMISSIONE - p["costo"]) for t, p in pos.items())
    neg_g = sum(min(0, p["azioni"] * chiusure_ult[t] - COMMISSIONE - p["costo"]) for t, p in pos.items())
    netto_lat = max(0.0, pos_g + neg_g - fisco.credito_residuo(anno))
    finale_netto = equity[-1] - len(pos) * COMMISSIONE - netto_lat * ALIQUOTA

    return dict(
        nome=nome, equity=pd.Series(equity, index=date), esposizione=pd.Series(esposizione, index=date),
        operazioni=pd.DataFrame(operazioni), finale_netto=finale_netto,
        commissioni=tot_comm, tasse=fisco.pagato + netto_lat * ALIQUOTA, dividendi=tot_div,
        aperte=len(pos), riduzioni=n_riduzioni, peso_max=peso_max_visto,
    )


def buy_and_hold(adj_netto: pd.DataFrame, chiu: pd.DataFrame, inizio):
    """Equal weight fisso sui titoli già quotati all'inizio, dividendi netti reinvestiti."""
    titoli = [t for t in adj_netto.columns
              if t != BENCHMARK and not np.isnan(adj_netto.at[inizio, t])]
    norm = adj_netto.loc[inizio:, titoli] / adj_netto.loc[inizio, titoli]
    eq = CAPITALE * norm.mean(axis=1)
    finale = eq.iloc[-1] - max(0.0, eq.iloc[-1] - CAPITALE) * ALIQUOTA
    return eq, finale, len(titoli)


def metriche(eq: pd.Series, finale_netto: float) -> dict:
    anni = (eq.index[-1] - eq.index[0]).days / 365.25
    rend = eq.pct_change().dropna()
    dd = (eq / eq.cummax() - 1).min()
    return {
        "Finale netto (€)": finale_netto,
        "CAGR netto": (finale_netto / eq.iloc[0]) ** (1 / anni) - 1,
        "Max drawdown": dd,
        "Volatilità ann.": rend.std() * np.sqrt(252),
        "Sharpe (rf=0)": rend.mean() / rend.std() * np.sqrt(252) if rend.std() > 0 else np.nan,
    }


def cagr_periodo(eq: pd.Series, a: str, b: str) -> float:
    s = eq.loc[a:b]
    anni = (s.index[-1] - s.index[0]).days / 365.25
    return (s.iloc[-1] / s.iloc[0]) ** (1 / anni) - 1


def main():
    global BENCHMARK, UNIVERSO_TICKER
    mercato = sys.argv[1] if len(sys.argv) > 1 else "italia"
    UNIVERSO_TICKER = list(leggi_universo(ROOT / UNIVERSI[mercato]))
    BENCHMARK = UNIVERSO_TICKER[0]
    adj_full, chiu_full, aper_full, adj_netto, div = matrici()
    tickers_azioni = [t for t in adj_full.columns if t != BENCHMARK]
    # la simulazione gira dal 2002 ma non apre posizioni finché non c'è una SMA200:
    # le metriche partono dal primo giorno in cui esiste almeno una media a 200 gg
    inizio = adj_full[tickers_azioni].rolling(200).mean().dropna(how="all").index[0]
    adj, chiu, aper, adj_netto = (m.loc[inizio:] for m in (adj_full, chiu_full, aper_full, adj_netto))
    print(f"Mercato: {mercato} (benchmark {BENCHMARK})")
    print(f"Periodo backtest: {inizio:%Y-%m-%d} -> {adj.index[-1]:%Y-%m-%d}  "
          f"({len(tickers_azioni)} titoli, universo = composizione attuale)\n")

    risultati = {}
    for nome in ("A", "B"):
        r = simula(nome, adj_full, chiu_full, aper_full, div)
        r["equity"] = r["equity"].loc[inizio:]
        r["esposizione"] = r["esposizione"].loc[inizio:]
        risultati[nome] = r

    eq_bh, fin_bh, n_bh = buy_and_hold(adj_netto, chiu, inizio)
    idx = chiu[BENCHMARK].dropna()
    eq_idx = CAPITALE * idx / idx.iloc[0]
    fin_idx = eq_idx.iloc[-1] - max(0.0, eq_idx.iloc[-1] - CAPITALE) * ALIQUOTA

    righe = {
        "A: sopra SMA200": metriche(risultati["A"]["equity"], risultati["A"]["finale_netto"]),
        "B: breakout 50/20": metriche(risultati["B"]["equity"], risultati["B"]["finale_netto"]),
        f"B&H equal-weight ({n_bh} titoli)": metriche(eq_bh, fin_bh),
        f"{BENCHMARK} (solo prezzo, no div.)": metriche(eq_idx, fin_idx),
    }
    tab = pd.DataFrame(righe).T
    pd.set_option("display.width", 200)
    pd.options.display.float_format = "{:,.3f}".format
    print(tab.to_string())

    print("\nDettaglio strategie")
    for n in ("A", "B"):
        r = risultati[n]
        op = r["operazioni"]
        print(f"  {n}: operazioni chiuse {len(op)} (+{r['aperte']} aperte), "
              f"vincenti {(op.guadagno > 0).mean():.0%}, durata media {op.giorni.mean():.0f} gg, "
              f"esposizione media {r['esposizione'].mean():.0%}, "
              f"commissioni {r['commissioni']:,.0f}€, tasse {r['tasse']:,.0f}€, "
              f"dividendi netti incassati {r['dividendi']:,.0f}€, "
              f"riduzioni peso {r['riduzioni']}, peso max a fine giorno {r['peso_max']:.0%}")

    print("\nCAGR lordo-curva per sotto-periodo (stabilità):")
    metà = adj.index[len(adj) // 2]
    for lab, a, b in (("1ª metà", adj.index[0], metà), ("2ª metà", metà, adj.index[-1])):
        riga = {n: cagr_periodo(r["equity"], a, b) for n, r in risultati.items()}
        riga["B&H"] = cagr_periodo(eq_bh, a, b)
        riga["Benchmark"] = cagr_periodo(eq_idx.loc[inizio:], a, b)
        print(f"  {lab} ({a:%Y}-{b:%Y}): " + "  ".join(f"{k} {v:+.1%}" for k, v in riga.items()))

    out = ROOT / "risultati" / mercato
    out.mkdir(parents=True, exist_ok=True)
    curve = pd.DataFrame({"A": risultati["A"]["equity"], "B": risultati["B"]["equity"],
                          "BH_EW": eq_bh, "FTSEMIB": eq_idx.loc[inizio:]})
    curve.to_csv(out / "curve_equity.csv", float_format="%.2f")
    for n in ("A", "B"):
        risultati[n]["operazioni"].to_csv(out / f"operazioni_{n}.csv", index=False, float_format="%.4f")
    print(f"\nCurve e operazioni salvate in {out}")


if __name__ == "__main__":
    sys.exit(main())
