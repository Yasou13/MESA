# 8. Benchmark mimarisi: retrieval ve ortak Full‑QA

Date: 2026-07-17
Status: Accepted and implemented

## Context

Retrieval-only sonuçlar bir bellek sisteminin doğru bağlamı bulup bulmadığını gösterir fakat son kullanıcı cevabını ölçmez. Her adapter’ın kendi generator’ını kullanması ise retrieval sistemiyle model kalitesini birbirine karıştırır.

## Decision

Benchmark iki bağımsız hat üretir:

1. Adapter yalnızca sıralı Top‑5 context ve retrieval latency döndürür.
2. Runner bu context’leri bütün sistemler için aynı Ollama generator’a verir ve Full‑QA cevabını üretir.

`BenchmarkResponse` context payload, retrieval latency, generation latency ve token kullanımını ayrı taşır. Eski alanlar uyumluluk için korunur. MESA adapter’ı generation yapmaz; semantic judge da retrieval adapter’ının parçası değildir.

Full‑QA normalized EM, token F1 ve şemalı semantic judge ile ölçülür. Generator ile aynı judge yalnızca provisional kanıttır. Purge/ingest/query/generation/judge hatası bulunan koşum geçersizdir.

## Consequences

- Bellek sistemleri aynı generation koşulunda karşılaştırılır.
- Retrieval ve generation darboğazları ayrı raporlanır.
- Full‑QA koşumları Ollama erişimi ve ek süre gerektirir.
- Harici dataset context relevance etiketi vermiyorsa retrieval metriği uydurulmaz; `N/A` raporlanır.
- Yayınlanabilir sonuç için bağımsız judge, external dataset provenance ve sıfır altyapı hatası gerekir.
