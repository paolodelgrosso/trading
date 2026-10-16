"""Aggiornamento serale non presidiato: scarico + verifica, con log mensile in logs/.

Pensato per Task Scheduler (giorni feriali, dopo le 18). Codice di uscita 0 = ok,
1 = errori (dettagli nel log). Nel weekend non fa nulla.
"""
import os
import subprocess
import sys
from datetime import datetime

from db import ROOT

LOG_DIR = ROOT / "logs"
SRC = ROOT / "src"


def esegui(script: str, log) -> int:
    log.write(f"\n--- {script} ---\n")
    log.flush()
    r = subprocess.run([sys.executable, str(SRC / script)], cwd=SRC,
                       stdout=log, stderr=subprocess.STDOUT,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode


def main() -> int:
    adesso = datetime.now()
    if adesso.weekday() >= 5:
        return 0
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / f"scarico_{adesso:%Y-%m}.log", "a", encoding="utf-8") as log:
        log.write(f"\n===== {adesso:%Y-%m-%d %H:%M:%S} =====\n")
        rc = esegui("scarica_prezzi.py", log)
        rc |= esegui("verifica.py", log)
        log.write(f"\nEsito: {'OK' if rc == 0 else 'ERRORI'}\n")
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
