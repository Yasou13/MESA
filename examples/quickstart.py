from pathlib import Path

from mesa_client import Mesa

mesa = Mesa()
mesa.add(Path("kira.txt").read_text(), source="file://kira.txt", wait=True)
print(mesa.context("kira ne zaman ödenir?"))
