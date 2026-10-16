import pandas as pd

from db import DB_PATH, connetti

conn = connetti()
df = pd.read_sql(
    "SELECT ticker, righe, prima_data, ultima_data, ultimo_update "
    "FROM aggiornamenti ORDER BY ticker",
    conn,
)
conn.close()

pd.set_option("display.width", 120)
print(df.to_string(index=False))
print(f"\nTicker: {len(df)}   Righe totali: {df['righe'].sum():,}")
print(f"Dimensione DB: {DB_PATH.stat().st_size / 1_048_576:.1f} MB")