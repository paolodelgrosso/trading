import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "trading.db"

# Prima del 2002 (lira) i dividendi di Yahoo sono in lire su prezzi in euro:
# la chiusura_adj di Yahoo è inservibile. Il backtest legge solo da qui in poi.
DATA_INIZIO_BACKTEST = "2002-01-01"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prezzi_giornalieri (
    ticker        TEXT    NOT NULL,
    data          TEXT    NOT NULL,   -- YYYY-MM-DD
    apertura      REAL,
    massimo       REAL,
    minimo        REAL,
    chiusura      REAL,               -- prezzo reale, non rettificato
    chiusura_adj  REAL,               -- rettificato per dividendi e split
    volume        INTEGER,
    PRIMARY KEY (ticker, data)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS dividendi (
    ticker       TEXT NOT NULL,
    data_stacco  TEXT NOT NULL,           -- YYYY-MM-DD (ex-date)
    importo      REAL NOT NULL,           -- lordo, per azione, come da Yahoo
    PRIMARY KEY (ticker, data_stacco)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS aggiornamenti (
    ticker         TEXT PRIMARY KEY,
    ultimo_update  TEXT NOT NULL,
    righe          INTEGER NOT NULL,
    prima_data     TEXT,
    ultima_data    TEXT
);
"""


def connetti() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn