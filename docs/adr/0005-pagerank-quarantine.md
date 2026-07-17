# 5. PageRank Quarantine (Epistemic Uncertainty Sönümleme)

Date: 2026-07-17

## Status

Accepted

## Context

MESA'nın bellek grafiğinde, doğruluğu belirsiz (epistemic uncertainty) veya düşük güvenilirliğe sahip düğümlerin ağ genelinde PageRank değerlerini orantısız şekilde artırması riski mevcuttur. Bu durum, hatalı veya eksik bilginin arama sonuçlarını domine etmesine yol açabilir.

## Decision

PageRank algoritmasına bir "karantina" mekanizması ekledik. Yeni eklenen veya düşük güven skoruna sahip düğümler, belirli bir güven eşiğine (epistemic uncertainty sönümleme) ulaşana kadar PageRank hesaplamasında cezalandırılır veya geçici bir karantina havuzunda tutulur.

## Consequences

- **Positive:** Güvenilir olmayan bilgilerin grafikteki merkeziyeti (centrality) suni olarak şişirilemez.
- **Negative:** Karantina süresi boyunca yeni eklenen geçerli bilgilerin de keşfedilebilirliği geçici olarak düşük kalabilir.
