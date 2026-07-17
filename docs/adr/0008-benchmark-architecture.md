# 8. Benchmark Mimarisi: Retrieval-Only vs Full QA

Date: 2026-07-17

## Status

Accepted

## Context

MESA'nın benchmark (mesa-benchmark) sistemi, önceleri sadece retrieval metriklerine (Hit@K, vb.) odaklanıyordu (Retrieval-Only). Ancak bu yaklaşım, sistemin uçtan uca doğruluğunu ve LLM'in getirilen bağlamı sentezleme yeteneğini ölçmekte yetersiz kaldı. Çok aşamalı (multi-hop) ve zıtlık (contradiction) senaryoları tam QA gerektiriyordu.

## Decision

Benchmark mimarisini tam (Full QA) bir yapıya geçirmeye karar verdik. `mesa_client.py`'deki `answer()` metodu, sadece alınan bağlam bloklarını döndürmek yerine, gerçek bir LLM generation çağrısı (`acomplete`) yaparak cevabı sentezler ve prompt/completion token sayılarını rapora ekler.

## Consequences

- **Positive:** Sistem değerlendirmesi, uçtan uca kullanıcı deneyimiyle (ve Cognee, Mem0 gibi rakiplerle) aynı standartta olur.
- **Negative:** Benchmark'ın çalışma süresi (latency) ve maliyeti (LLM token'ları) önemli ölçüde artar.
