# CURRENT PHASE

- **Aktif faz:** Faz 9 — Kontrollü remediation (deployment dokümantasyonu, kalite kapıları ve dependency determinism)
- **Durum:** Devam ediyor
- **Tarih:** 2026-07-21
- **Son görev:** Doğrulanan README/API sözleşmesi, dotenv-auth, retrieval response ve Python 3.13 coverage remediation'ı
- **Sıradaki görev:** Cold-path iş sahipliği ve worker-only tüketicisinin topoloji kararına bağlı uygulanması
- **Blocker:** API'nin durable producer, worker'ın tek tüketici olması için model-disabled Compose profilinin ingestion garantisi ve worker'ın graph/vector storage sahipliği kararlaştırılmalıdır
- **Düzeltilen bulgular:** DOC-003, DOC-004, CI-003, SEC-004; OPS-001 için lock/Docker/ana kalite kapısı uygulanmıştır
- **Kod değişikliği izni:** Bu remediation tamamlandı

## 2026-07-21 — Doğrulanmış açık bulgu remediation'ı

- README health/status/session örnekleri gerçek route ve authentication sözleşmesine eşitlendi.
- Explicit dotenv yüklemesinden sonra API auth ayarları yeniden okunur.
- Search response, retriever source score'unu ve memory içeriğini taşır; structured logging dışı traceback/print çağrıları kaldırıldı.
- Coverage job'ı Python 3.10 ve Docker'daki Python 3.13 ile çalışacak şekilde matrix'e alındı.
- Cold-path iş sahipliği API'den ayrı durable worker'a taşındı; gerçek Compose process testi sandbox test koşusunun kesilmesi nedeniyle henüz doğrulanmadı.
- Insert API'nin dış sözleşmesi durable kabulü açıkça belirtmek üzere `202 {"status":"queued", "log_id", "processing_mode":"async"}` olarak standardize edildi. İç raw-log durumu `DEFERRED` ve dispatch receipt sonucu `ENQUEUED` olarak worker/storage sınırında kalır.

## Faz 0 çıkış kriterleri

- Önce başarısız docs-contract testi yazıldı: tamamlandı.
- Minimum dokümantasyon, CI, Docker dependency ve governance düzeltmeleri uygulandı: tamamlandı.
- Dar testler, full-repo Ruff, production mypy, lock ve Docker Python 3.13 import doğrulandı: tamamlandı.
