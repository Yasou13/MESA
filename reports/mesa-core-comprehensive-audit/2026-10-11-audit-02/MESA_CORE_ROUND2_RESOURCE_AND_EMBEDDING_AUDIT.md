# MESA Core Round 2 — Kaynak ve Embedding Denetimi

## Representation sözleşmesi

| Katman | Değer | Anlam |
|---|---|---|
| SQL assertion | `assertion-v1` | canonical assertion payload sürümü |
| Vector representation | `assertion-semantic-v1` | document/query semantic text sözleşmesi |
| Registry artifact | `ASSERTION_VECTOR` | fiziksel artifact türü |

`v4-retrieval-v1` canlı kaynakta writer/consumer sabiti değildir; yalnız eski rapor
nomenklatürüydü. Bu üç canlı değer farklı katmanları adlandırır, mismatch değildir.

Consumer aşağıdaki alanların tamamını fail-closed karşılaştırır:

- `embedding_space_id`
- provider/model/model_revision(version/digest authority)
- version/dimension/normalized
- `embedding_dimension`
- representation version ve registry ID

Gerçek LanceDB testleri compatible vector'ın sorguya/RRF'ye katkısını; wrong
dimension, different embedding space/model revision ve wrong representation'ın
reddini doğruladı. Rebuild testinde registry representation kasıtlı `legacy-v0`
yapıldı, fresh 768-dim embedding ile replay edildi ve cutover sonrası yalnız
`assertion-semantic-v1` ACTIVE kaldı. Metadata-only relabel rebuild sayılmadı.

## Provider incelemesi

- Varsayılan model: `magibu/embeddingmagibu-200m`
- Varsayılan dimension: `768`
- Normalization: `true`
- Yerel hostta Ollama binary/service bulunmadı; `127.0.0.1:11434/api/tags` bağlantısı
  reddedildi.
- Context7 güncel Ollama belgesine göre `/api/embed` body `model` + `input` kullanır
  ve L2-normalized vector döndürür; `input_type` alanı belgelenmez.
- MESA'nın OpenAI-compatible backend'i `input_type/truncate` yalnız
  `uses_nemotron_asymmetric_profile(...)` doğruysa ekler; Magibu için icat edilmiş
  Nemotron `input_type` göndermez.

Gerçek Magibu model digest'i ve provider cevabı bu hostta çözülemedi. Sonuç:
`LIVE_PROVIDER_QUALITY_NOT_RUN`; deterministic/fake embedding testleri native provider
kalitesi olarak sunulmadı.

## Kaynak/bellek

- `test_embedded_soak_run_integration`: 5 saniye, 2 RPS, gerçek V4 lifecycle;
  correctness/scope/drain PASS ve bilerek `SMOKE PASS — NOT CERTIFICATION`.
- `test_audit_v20_resource_smoke` ve vector maintenance/native fixture'lar PASS.
- Historical yaklaşık +402 MB / 1.000 upsert iddiası için bu hostta 1.000-op/8GB veya
  uzun süreli native soak çalıştırılmadı. Fragment/version/RSS stabilitesi çözülmüş
  ilan edilmedi.

Nihai kaynak hükmü: `SOAK_NOT_RUN` (sertifikasyon ölçeği). Profile A/VM üzerinde RSS,
USS, fragments, versions, FD, throughput ve p50/p99 ölçümü halen gereklidir; 7 günlük
retention veya `delete_unverified=True` değiştirilmemiştir.
