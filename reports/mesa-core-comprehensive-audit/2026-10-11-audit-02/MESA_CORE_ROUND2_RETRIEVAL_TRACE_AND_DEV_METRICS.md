# MESA Core Round 2 — Retrieval İzleri ve DEV Ölçümleri

## Canlı veri yolu

```text
typed API payload
→ authenticated session/dataset scope
→ physical catalog IDs
→ mutation + COMMITTED fence
→ rich extraction veya legal-aware fallback
→ canonical v4_assertions + FTS + vector + graph
→ pre-rank scope/temporal allow-list
→ indexed legal candidate recall
→ vector | BM25 | assertion | graph lane order
→ weighted RRF (1/1/1/1, k=60)
→ bounded legal factor
→ top-K + canonical provenance hydration
→ product ContextBuilder
```

## Minimal onarım metriği

Aynı iki frozen reproducer, `K=1`:

| DEV vaka | Baseline `348efe8` | Patched | Eski kaldırılma/kayıp nedeni |
|---|---:|---:|---|
| bare article + trusted TBK/1 fallback | FAIL | PASS | assertion'da typed kolon yok |
| verified TBK/1 vs heading-only distractor | MISS@1 | HIT@1 | doğru kayıt FTS pool öncesi kayıp |

Bu iki sorguda ölçülebilen değerler:

- Hit@1: `0/2 = 0.00` → `2/2 = 1.00`
- MRR: `0.00` → `1.00`
- Legal false-positive winner: `1/2` → `0/2`
- Scope violation: `0` → `0`

İki sorguluk ölçüm resmi benchmark değildir ve nDCG genellemesi için yeterli değildir.

## Dondurulmuş DEV manifesti

`tests/fixtures/v4_round2_legal_dev.json` on kategori ve `top_k=5` ile kilitlendi.
Resmi Profile B içermez. Query/qrel manifesti retrieval çıktısından üretilmedi.

| Kategori | Yerel doğrulama | Sonuç |
|---|---|---|
| DIRECT_ARTICLE_ID | Round 2 rank reproducer | PASS |
| SEMANTIC_PARAPHRASE | phase4 + comprehensive retrieval | PASS |
| FALSE_FRIEND | TBK/TCK same-article negative | PASS |
| ALIAS_DISAMBIGUATION | canonicalizer + resolver suites | PASS |
| TEMPORAL_VERSION | yeni holdout current/as-of | PASS |
| MULTI_HOP_COMPLETE | real 2/3-hop + atomic graph proof suites | PASS |
| GRAPH_SCOPE_NEGATIVE | tenant/dataset/foreign-hop suites | PASS |
| NO_ANSWER | scoped empty/fail-closed suites | PASS |
| RESTART_PROJECTION | restart holdout + full rebuild/cutover lifecycle | PASS |
| VECTOR_SPACE_MISMATCH | real Lance consumer/rebuild mismatch suites | PASS |

Bu kategoriler aynı tek metrik runner'ında birleşmediği için 10-query nDCG/Recall değeri
üretilmedi; uydurma toplam yazılmamıştır. Graph complete evidence kanıtı unit düzeyi
mock değil, mevcut gerçek Kùzu fixture'larıyla; vector mismatch/activation kanıtı gerçek
LanceDB fixture'larıyla çalışmıştır.

## Lane ve fusion izi

- BM25 passage FTS bounded candidate pool üretir; bu resmi beşinci lane değildir.
- Typed selector yalnız canonical assertion kolonlarını ve compound index'i kullanır.
- Lane skorları lexical `+200/+40`, assertion `+150/+40` semantiğini korur.
- `rrf_fuse_lanes` aynı candidate'ı lane içinde deduplicate eder, lane isimlerini
  sıralı toplar ve eşit skorda candidate ID ile deterministik sıralar.
- Graph provenance supporting assertion ve path kimliklerini SQL canonical IDs ile
  uzlaştırır; hydration evidence ID'yi entity ID ile değiştirmez.
