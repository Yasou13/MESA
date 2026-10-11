# MESA Core Round 2 — Bulgular ve Kök Nedenler

## R2-P0-01 — Fallback projection yasal üst kimliği kaybediyordu

- **Durum:** FIXED
- **Eski kanıt:** `test_fallback_preserves_trusted_parent_law_identity` eski uçta
  `KeyError: law_id` ile; `test_trusted_legal_identity_is_used_before_rank` ise
  heading-only distractor'ı birinci döndürerek FAIL verdi.
- **Kök neden:** mutation metadata canonical assertion kolonlarına ve fallback graph
  kimliğine taşınmıyordu. Bare `MADDE 1` parent law'u geri kuramaz.
- **Onarım:** additive migration ile `law_id`, `statute_code`, `article_id`,
  paragraph/clause/version/source kolonları; legal-aware fallback; hydrated provenance.
- **Negatif karşıörnek:** aynı madde numaralı TBK/TCK, yanlış madde, bilinmeyen alias,
  sahte `_mesa_*` metadata.
- **Risk:** kaynak gerçeği producer tarafından doğrulanmalıdır.

## R2-P0-02 — Trusted metadata top-K öncesinde aday olamıyordu

- **Durum:** FIXED
- **Kök neden:** citation text scoring yalnız FTS pool'a girdikten sonra çalışıyordu;
  parent law metinde yoksa doğru assertion rank aşamasına ulaşamıyordu.
- **Onarım:** `idx_v4_assertions_legal_identity_scope` üzerinden tenant/dataset/agent,
  COMMITTED, lifecycle, jurisdiction ve temporal filtreleriyle indexed pre-rank seçim.
- **Sınır:** belirli madde sorgusu exact `(statute_code, article_id)` seçer; madde
  içermeyen kanun sorgusu aynı statute için bounded seçim yapar.

## R2-P0-03 — Yeni migrasyon rebuild preflight'i kırdı

- **Durum:** FIXED
- **RED:** `test_restart_safe_admission_migration_rebuild_cutover_lifecycle` yeni
  DB'nin `d5e6...` head'ini eski `c4d5...` sabitine karşı reddetti.
- **Onarım:** `REBUILD_ALEMBIC_HEAD=d5e6f7a8b9c0`; preparation/closure beklentileri
  güncellendi.
- **GREEN:** lifecycle, preparation, runner ve runbook kümesi 18/18 geçti.

## R2-P1-04 — Public contract belgesi eksikti

- **Durum:** FIXED
- **Kanıt:** bağımsız standards review, `CONTRIBUTING.md` public contract/changelog
  kuralını ihlal olarak işaretledi.
- **Onarım:** `docs/architecture-v4.md`, `docs/api-reference.md`, `CHANGELOG.md`.

## R2-AUDIT-05 — Dört lane ve RRF

- **Durum:** VERIFIED_NO_NEW_DEFECT
- Resmi lane'ler vector/BM25/assertion/graph; passage FTS BM25'in iç alt yoludur.
- `V4_RRF_LANE_WEIGHTS` dört lane için `1.0`; `k=60`; duplicate backend hit tek oy;
  tie-break `candidate_id` ile deterministiktir.
- Citation değerleri lane sırasını etkiler; RRF skoruna doğrudan eklenmez. Final skor
  `rrf_score * legal_factor` olarak hesaplanır.
- 1948-test tam koşu ve dört-lane/graph adversarial kümelerinde yeni kusur bulunmadı.

## R2-AUDIT-06 — Graph complete evidence

- **Durum:** VERIFIED_NO_NEW_DEFECT
- Gerçek Kùzu 2/3-hop, dataset/tenant izolasyonu, stale bridge, duplicate path,
  graph ON/OFF ve atomic ContextBuilder proof testleri geçti.
- Historical VM `graph PASS / relational completeness 0` gözlemi yerel olarak
  graph ağırlığı kusuruna indirgenemedi; resmi capture yoktur.

## R2-EXT-07 — Producer doğruluğu

- **Durum:** EXTERNAL_DEPENDENCY
- MESA Core typed alanı tanınan kanun alias'ına, authenticated session/dataset
  writer scope'una ve canonical mutation provenance'ına bağlar; serbest metadata
  boost alamaz.
- MESA Core dış kaynağın içerik olarak doğru kanun/madde olduğunu tek başına
  kanıtlayamaz. MESA_Data yalnız doğrulanmış katalog kimliği göndermelidir.

## Eski rapor SHA notu

Round 1 prompt/raporlarında geçen `04c2a70d8a5...` resolve olmamaktadır. Gerçek,
korunan ancestor commit `04c2a7016a151c5f2dab0374dc55098780acb4b8`'dir.
