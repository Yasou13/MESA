import sys

sys.path.append(".")
from mesa_evals.contradiction_runner import evaluate_keywords

context = """[Entity: benchmark_subject]
[Type: ENTITY]
[Created: 2026-06-28T23:09:54.673895]
Content: Metal-İş Sendikası ile Demir Çelik Üreticileri İşveren Sendikası arasında 01.01.2022 tarihinde yürürlüğe giren 2 yıl süreli toplu iş sözleşmesi (TİS) uyarınca, metal sektöründeki işçilerin saat başı ücreti 85 TL, yemek yardımı günlük 45 TL ve yıllık ikramiye 4 maaş tutarında belirlenmiştir. TİS'in 23. maddesi grev yasağı süresini düzenlemiş olup, sözleşme süresi boyunca grev ve lokavt uygulanamayacağı hükme bağlanmıştır."""

keywords = ["120 TL", "65 TL", "yeni TİS", "01.03.2024", "güncellenen koşullar"]

hits, misses, score, match = evaluate_keywords(context, keywords)
print("Hits:", hits)
print("Misses:", misses)
print("Match:", match)
