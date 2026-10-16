# Progetto trading semi-automatico

## Contesto
- Capitale ipotetico 100.000 €. Lavoro in orario di borsa, non seguo il mercato.
- Broker: Directa SIM, regime amministrato, 5 €/ordine fissi.
- API Directa (Darwin aperta sul PC): socket locali 10001 prezzi, 10002 trading,
  10003 storico; protocollo testuale (es. `ACQAZ ID,TICKER,QTA,PREZZO`).
  Disconnessione notturna, max 100 titoli realtime, nessun conto di test ufficiale
  (verificare opzione `-test` della versione a riga di comando DCL).
- Approccio: niente intraday. Posizioni di giorni o settimane su azioni FTSE MIB,
  dati giornalieri, segnali calcolati la sera, ordini limite la mattina prima dell'apertura.
- Strategie da testare:
  1. Trend: sopra media 200 gg / rottura massimo 50 gg.
  2. Mean reversion: titolo in trend positivo dopo 2-3 giorni di forte calo,
     uscita al primo rimbalzo.
  - Confronto con buy & hold.
- Fisco: 26%. Plusvalenze ETF = redditi di capitale, minusvalenze ETF = redditi diversi
  (non compensabili tra loro): trading attivo su azioni, ETF come benchmark.
  Il backtest deve modellare questa asimmetria.
- Rischi da evitare: sovra-ottimizzazione, bias di sopravvivenza (universo = composizione
  attuale FTSE MIB), campioni piccoli. Gap overnight: max 10-15% del capitale per titolo,
  niente leva, stop valutati sulla chiusura.

## Stack
- Windows, PowerShell, Python 3.12 in `.venv`, pandas 3.0.6, yfinance 1.7.0.
- SQLite in `data/trading.db` (soluzione leggera: niente Postgres/container, il PC è carico).
- File:
  - `src/db.py` — schema: `prezzi_giornalieri`, `aggiornamenti`
  - `src/scarica_prezzi.py` — riscarica lo storico completo per ticker perché `adj_close` cambia all'indietro
  - `src/verifica.py` — controlli di qualità sui dati
  - `src/adjust.py` — `carica_prezzi()`: prezzi dal 2002-01-01 con `chiusura_adj` ricalcolata da `chiusura` + tabella `dividendi` (la colonna Yahoo diventa `chiusura_adj_yahoo`). Il backtest deve leggere SEMPRE da qui, non da `prezzi_giornalieri` direttamente.
  - `src/aggiorna_sera.py` — scarico + verifica con log in `logs/scarico_AAAA-MM.log` (escluso da git); salta il weekend, exit 1 se ci sono errori. Pianificato in Task Scheduler come `Trading-ScaricoSerale`, lun-ven 18:30 (utente connesso, `StartWhenAvailable`). Non gestisce le festività di borsa (riscarica senza novità). Nessuna notifica: controllare il log.
  - `src/confronta_adj.py` — confronto adj nostra vs Yahoo
  - `universo.csv` — ticker Yahoo `.MI` + `FTSEMIB.MI` come benchmark
- Dati pre-2002 inutilizzabili: Yahoo applica dividendi in lire a prezzi in euro e la sua `chiusura_adj` va a zero/negativa (DB invariato, filtro in lettura, `DATA_INIZIO_BACKTEST` in `db.py`).
- STMMI.MI, TEN.MI: dividendi pagati in USD. yfinance li converte in EUR al cambio SPOT di oggi, l'adj di Yahoo usa l'importo USD come se fosse EUR: nessuno dei due è corretto. Correzione implementata (colonna `div_valuta=USD` in `universo.csv`): si inverte la conversione con lo spot (importo×spot = cifre tonde in USD) e si riconverte con EURUSD=X alla data di stacco. Per questi due ticker l'adj nostra DEVE differire da quella di Yahoo (confronto non più valido). Limite: EURUSD=X parte il 2003-12-01, i 5 dividendi precedenti usano il cambio di quella data (effetto trascurabile). Non usare i ticker USA STM/TS: TS è un ADR da 2 azioni e ha buchi.
- AZM.MI: due dividendi nel 2005-05 (uno con data domenica 22/05); Yahoo lo sposta a venerdì, noi a lunedì (più corretto). Effetto solo su 2 giorni, total return identico: ignorabile.
- Colonne prezzi: `chiusura` (reale, per gli ordini) e `chiusura_adj` (per i rendimenti).

## Git
- Identità locale: paolodelgrosso <paolo.delgrosso@gmail.com>.
  La globale è quella lavorativa: non toccarla.
- Branch `master`; `.venv/` e `data/` (DB SQLite) sono esclusi da `.gitignore`.

## Modo di lavorare
- Un passo alla volta, spiegare cosa si fa.
- Risposte in italiano.
