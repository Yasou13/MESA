from mesa_client import Mesa

mesa = Mesa()
mesa.add(open("kira.txt").read(), source="file://kira.txt")
print(mesa.context("kira ne zaman ödenir?"))
