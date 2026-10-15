# Bağımsız fact-check ve standards incelemesi

Sabit karşılaştırma: `8ddd2dc04cf697fe237e7a49e391543c01182d83...b83aff4188c1a59e8294d5f6587451a1bd08160a`. Code-review skill iki ayrı salt-okunur inceleme ile uygulandı. OOM sonrasında ajanlar sırayla çalıştı; test/build yapmadı. Durumlar önce SOURCE/TEST_CORRESPONDENCE; fresh execution ayrıca bounded JUnit kayıtlarına bağlıdır.

## Spec

İki bulgu: tokenizer yokken fail-closed iddiası yanlıştı (OpenAI counting UTF-8 byte üst sınırına dönüyor); VectorEngine low-level scope/identity zorunluluğu abartılmıştı. API/retrieval guide ve vector docstring bu gerçek davranışa düzeltildi. Runtime davranışı değiştirilmedi.

| Kritik iddia | Kaynak/test | Bağımsız sonuç |
|---|---|---|
| Model-disabled V4 admission reject | server.py/admission.py; test_p0_worker_ownership.py | MATCH |
| Insert title/bounds | v4_router.py; test_v4_api_contract.py | MATCH |
| Invalid temporal range | v4_router.py; test_v4_temporal_request_schema.py | MATCH |
| Wait terminal failure states | client.py; test_v4_sdk_contract.py | MATCH |
| HTTP legal identity; SDK lacks parameter | v4_router.py/client.py; legal identity/SDK tests | MATCH |
| Idempotent durable admission | dao.py; test_v4_ingestion_contract.py | MATCH |
| Frozen manifest activation | dao.py; lifecycle contract tests | MATCH |
| Semantic rejection cannot replay | v4_router.py; lifecycle tests | MATCH |
| Scope pre-rank | dao.py; retrieval_scope tests | MATCH |
| RRF one vote / rank1 | retrieval_scope.py; RRF contracts | MATCH |
| Graph20seeds/3hops | dao.py; graph contracts | MATCH |
| Stale graph evidence excluded | dao.py; graph hardening tests | MATCH |
| Backend unavailable503 | V4 router; API/graph tests | MATCH |
| Offline tokenizer behavior | tokenizer.py:44; test_r6_context_security_correctness.py | CORRECTED: conservative byte fallback |
| Low-level vector scope optional | vector_engine.py; search/constructor signatures | CORRECTED: canonical callers enforce scope |

## Standards

Üç actionable bulgu: (1) smoke bare assert Python -O altında sahte PASS üretebilirdi; explicit require/RuntimeError ile değiştirildi, -O negative probe ve restart smoke yapıldı: exit0. (2) DAO/LangChain public docstring agent=tenant mutlak garanti veriyordu; V3 compatibility ve V4 canonical boundary ayrıldı. (3) V3 router dao=/embedder= kullanım örneği, single INSERT/<50ms/separate-worker ve Alpha fusion iddiaları yanlıştı; dependency factory/durable admission/combined cold path/RRF açıklamaları düzeltildi, OpenAPI yeniden üretildi (47path).

184 dosya/1093 docstring girdisinde risk taraması yapıldı; her method implementation için bağımsız proof yapılmadı. Bu kapsam sınırı `NOT_VERIFIED` olarak korunur. Testler/security gate zayıflatılmadı. Core dışı repository yazılmadı.

Standards: 3 bulgu; en önemli yanlış PASS riski giderildi. Spec: 2 bulgu; en önemli tokenizer iddiası giderildi.

## Son yürütme sonucu


Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Standards3 ve spec2 bulgunun tamamı açıklama/örnek düzeyinde giderildi. OpenAPI47path yeniden üretildi. `python -O scripts/documentation_smoke.py` ve optimize negative probe exit0; restart öncesi kayıtlar tekrar okunarak kalıcılık doğrulandı. Son 1948 testin tamamı geçti; review satırları source-test correspondence olup her iddia için canlı provider proof değildir. Runtime semantiği değiştirilmedi. Tam1093docstring proof halen NOT_VERIFIED.
