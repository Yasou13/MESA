# 6. Spreading Activation & Fan-Effect Tasarımı

Date: 2026-07-17

## Status

Accepted

## Context

Bağlamsal geri çağırma (retrieval) sırasında graf yapısı üzerinden bilgiyi bulmak için hangi yöntemin kullanılacağı belirlenmeliydi. Çok sayıda bağlantıya sahip düğümlerde (hub nodes), yayılımın çok genişlemesi ve alakasız bağlamların (noise) toplanması riski vardır (Fan-Effect).

## Decision

Bilgi aktivasyonu için sınırlı "Spreading Activation" kullanılmasına karar verildi. Fan-effect'i kontrol altına almak amacıyla, bir düğümden çıkan kenarların sayısı (out-degree) arttıkça, o düğüm üzerinden yayılan aktivasyon enerjisi logaritmik veya doğrusal olarak sönümlenir.

## Consequences

- **Positive:** Multi-hop aramalarında bağlamdan kopma ve gürültü (noise) engellenir.
- **Negative:** Kapsamlı ve çok bağlantılı genel kavramlardan alt kavramlara geçiş zorlaşabilir, aktivasyon eşiği iyi ayarlanmalıdır.
