# MESA Benchmark

MESA Benchmark, bellek/retrieval sistemlerini aynı girdiler, chunk sırası, Top‑K,
token bütçesi ve cevap üreticisiyle karşılaştıran, yeniden başlatılabilir bir
benchmark runner’ıdır. MESA, dense RAG, Mem0, Letta ve opsiyonel Zep
adaptörlerini destekler.

## MESA v4 benchmark sözleşmesi

`release` ve `research` suite’lerindeki MESA satırları
`MesaV4ClientAdapter` kullanır. Adapter her scenario için izole tenant,
workspace ve dataset catalog’u kurar; source chunk ve mutation oluşturur;
SQL→vector→graph outbox’ını işler ve aramayı aynı dataset sınırında gerçek RRF
ile yapar. Dönen context mutation, artifact ve kaynak provenance’ını taşır.

`legacy` ve bazı internal smoke config’leri, v3 lexical-core uyumluluğunu
ölçmek için `MesaClientAdapter` kullanmaya devam eder. V3 ile v4 sonuçları
aynı runtime etiketi altında birleştirilmemelidir.

Deterministik çekirdek lane-ablation evaluator’ı vector-only,
vector+BM25, vector+graph ve tüm-lane RRF MRR değerlerini raporlar:

```bash
python -m mesa_evals.v4_rrf_ablation --output results/v4-rrf-ablation.json
```

Bu küçük corpus regresyon kanıtıdır; harici release dataset sonucunun yerine
geçmez.

Suite, config, manifest ve küçük offline fixture’lar wheel içinde
`resource://` URI’leriyle taşınır. Büyük datasetler `data://` URI’leriyle
çözülür. Veri kökü sırasıyla `MESA_BENCHMARK_DATA_DIR`, source checkout’taki
`mesa-benchmark/datasets` ve kullanıcı cache dizinidir. Sonuç kökü
`--results-root`, `MESA_BENCHMARK_RESULTS_DIR`, repository `results/` ve kurulu
pakette çalışma dizini sırasıyla seçilir.

## Tek komutluk suite akışı

```bash
# Tamamen offline kalite kapısı
mesa-benchmark dataset-sync --suite smoke
mesa-benchmark suite-check --suite smoke
mesa-benchmark run-suite --suite smoke --results-root results

# Pinned harici verileri hazırla ve doğrula
mesa-benchmark dataset-sync --suite release
mesa-benchmark suite-check --suite release
mesa-benchmark dataset-sync --suite research
mesa-benchmark suite-check --suite research
```

## Yerel Benchmark Console

Benchmark planlama, deterministic sharding, canlı ilerleme, güvenli
duraklatma/devam ve sonuç karşılaştırması için:

```bash
cd mesa-benchmark/dashboard-ui
npm ci
npm run build
cd ../..
mesa-benchmark dashboard
```

Panel yalnız `http://127.0.0.1:8765` adresinde açılır. `Yeni Benchmark`
sihirbazı profile uygun ingest semantiğini uygular; çalışma sırasında görülen
metrikler verification tamamlanana kadar `Geçici` olarak işaretlenir.

`smoke` deterministik ve internal’dır. `release`, BEAM 128K,
LongMemEval_S cleaned ve MemoryAgentBench ana track’lerini MESA + dense RAG +
Mem0 + Letta ile çalıştırır. `research`, CC-BY-NC LoCoMo’yu ana ticari skordan
ayırır ve BEAM 500K/1M scale tracklerini MESA + dense RAG + Mem0 ile çalıştırır.
Resmî `pair_chunk` release protokolünden ayrı 512-token/64-overlap ortak
chunking ablation’ı da `research` içinde aynı üç sisteme uygulanır.
MemoryAgentBench Recsys, resmi item-ID `Recall@5` semantiğiyle yalnız ikincil
research track olarak raporlanır.
10M capacity verisi yalnız explicit opt-in ile üretilir:

```bash
MESA_BENCHMARK_ENABLE_10M=1 mesa-benchmark dataset-sync --suite research
mesa-benchmark run --config resource://configs/research/beam_10m_capacity.yaml
```

Capacity track’i daima internal’dır ve kalite/üstünlük skoru sayılmaz.
`run-suite` JSONL v3 evidence bundle üretir ve sonunda otomatik
`verify-results` çağırır. Mevcut bir bundle ayrıca şu komutla doğrulanır:

```bash
mesa-benchmark verify-results --bundle results/SUITE-ID/bundle.json
```

## Ölçüm sözleşmesi

Her soru iki ayrı hatta ölçülür:

- Retrieval: primary `Hit@1/3/5`, ikincil `Hit@10/20`, MRR, graded nDCG, complete recall,
  authoritative hit, forbidden/outdated rate ve required evidence group
  coverage. Relevance etiketi olmayan sorularda retrieval metrikleri `N/A` olur.
- Full‑QA: bütün sistemlerin getirdiği Top‑5 bağlam aynı Ollama generator’a verilir; normalized EM, token F1, semantic judge, generation latency ve token kullanımı ayrı kaydedilir. Rapor, deterministic doğruluk, semantic-judge doğruluğu ve bunların birleşik primary-evaluator doğruluğunu ayrı gösterir.

Her adaptörün çıktısı runner seviyesinde Top‑5’e kesilir. Purge, ingest, query, generation veya judge hatası skorlanabilir boş cevap değildir: koşum `invalid` olur ve CLI non-zero döner. Tek modelle veya bağımsız judge gerçekten çalıştırılmadan üretilen sonuç `provisional/self-judged`; generator’dan farklı judge ile hatasız sonuç `publishable` olur. Sentetik veri setleri her durumda iç regresyon verisidir.

## Kurulum

Proje kökünden:

```bash
python -m pip install -e '.[adapters,ml,benchmarks]'
mesa-benchmark --help
```

MESA semantic retrieval için önbellekte `sentence-transformers/all-MiniLM-L6-v2` bulunmalıdır. Model yüklenemezse hash/fallback embedding ile devam edilmez; setup hata verir.

## Ollama yapılandırması

IP ve model adı kodda sabit değildir:

```bash
export BENCHMARK_OLLAMA_URL='http://OLLAMA_HOST:11434'
export BENCHMARK_GENERATOR_MODEL='qweb:8b'
export BENCHMARK_JUDGE_MODEL='independent-judge:8b'
```

Tek URL’den `MESA_OLLAMA_URL`, `OLLAMA_HOST` ve `OPENAI_BASE_URL` türetilir. Model etiketi `/api/tags` sonucuyla tam eşleşmelidir.

```bash
mesa-benchmark config-check --config resource://configs/legacy/mini_mesa.yaml
mesa-benchmark dataset-check --config resource://configs/legacy/mini_mesa.yaml
mesa-benchmark ollama-preflight --config resource://configs/legacy/mini_mesa.yaml
```

`config-check`, canlı Full-QA için generator, Ollama URL’si ve bağımsız judge sözleşmesini ağ çağrısı yapmadan doğrular. `ollama-preflight` bunun ardından model etiketlerini ve şemalı JSON chat yanıtını doğrular. Config dosyalarındaki boş model alanları bilinçli placeholder’dır; yukarıdaki environment değişkenleri verilmeden `config-check` fail-fast sonlanır.

Yalnız tek modelle iç regresyon koşumu yapılacaksa aynı modeli generator/judge olarak kullanın ve config içindeki `runtime.require_independent_judge` değerini `false` yapın. Bu sonuç otomatik olarak `provisional/self-judged` kalır; dışarı yayımlanmaz.

## Önerilen çalışma sırası

```bash
# 1. MESA mini
mesa-benchmark run --config resource://configs/legacy/mini_mesa.yaml

# 2. Mem0 mini
mesa-benchmark run --config resource://configs/legacy/mini_mem0.yaml

# 3. Comprehensive
mesa-benchmark run --config resource://configs/legacy/default.yaml

# 4. Aynı seed'lerde baseline ve eşlenmiş soru karşılaştırması
python scripts/reproduce_benchmark.py \
  --config resource://configs/legacy/default.yaml \
  --baseline-config resource://configs/legacy/mem0.yaml \
  --seeds 42,43,44,45,46 \
  --output results/reproducibility_report.json
```

Her seed ayrı dizin, manifest ve state dosyası kullanır. Resume yalnızca effective config ve dataset SHA‑256 değerleri aynıysa yapılır; soru anahtarları append-only JSONL’den yeniden kurulur. State dosyası yalnız scenario checkpoint’lerinde yazılır.

## Veri kaynakları

- `comprehensive_200` ve türevleri sentetiktir; yalnızca iç regresyon için kullanılır.
- Her datasetin revision, raw/converted checksum, SPDX lisans, redistribution,
  izolasyon, ingest, chunking, metric ve sayım sözleşmesi kendi typed
  `manifest.json` dosyasında sabittir; `resource://manifests/SOURCES.json`
  yalnız indekstir.
- Harici raw/converted büyük dosyalar commit edilmez; `dataset-sync` ile pinned
  kaynaktan hazırlanır. BEAM v2 release verisi local data root’ta checksum ile
  korunur; wheel içine alınmaz.
- LoCoMo indirme/dönüştürme: `python mesa-benchmark/scripts/download_locomo.py`. Lisansı CC‑BY‑NC‑4.0 olduğundan ticari kullanım ayrıca değerlendirilmelidir.

Eski `mesa-benchmark/config_*.yaml` adları CLI tarafından geriye uyumlu alias
olarak kabul edilir ve deprecation uyarısı üretir. Yeni entegrasyonlar canonical
`resource://configs/...` adlarını kullanmalıdır.

## Docker

Build context repository kökü olmalıdır:

```bash
docker build -f mesa-benchmark/Dockerfile -t mesa-benchmark .
docker run --rm --env-file mesa-benchmark/.env \
  mesa-benchmark --config resource://configs/legacy/mini_mesa.yaml
```

Image semantic embedding modelini build sırasında önbelleğe alır.

## Test

```bash
PYTHONPATH=mesa-benchmark python -m pytest mesa-benchmark/tests -q
ruff check mesa-benchmark/mesa_benchmark mesa-benchmark/tests
mypy mesa-benchmark/mesa_benchmark
python -m pytest tests/test_v4_rrf_ablation.py -q
```

P95/P99 yalnız en az 20 latency gözlemi varsa raporlanır; küçük mini koşumlarda değer `N/A` olur. Multi-seed özet ve baseline karşılaştırması `N/A` değerlerini sıfır kabul etmez; kullanılan ve dışlanan seed’leri JSON raporunda belirtir.

## `mesa_evals` sınırı

`mesa_evals`, MESA çekirdeğinin sentetik/golden dataset ve CI regresyon paketidir. Bu CLI, MESA ile diğer memory sistemleri arasında karşılaştırma veya yayınlanabilir sonuç üretmez. Karşılaştırmalı sonuçlar yalnız `mesa-benchmark` ile ve bu belgedeki validity sözleşmesi altında üretilir.

Yerel sahte Ollama + gerçek geçici MESA storage entegrasyonu:

```bash
PYTHONPATH=mesa-benchmark \
MESA_RUN_SOCKET_TESTS=1 MESA_RUN_REAL_STORAGE_TESTS=1 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
python -m pytest mesa-benchmark/tests/test_hardening.py -q
```

Ayrıntılar için [kullanım kılavuzu](USAGE_GUIDE.md), [metodoloji](../BENCHMARK_METHODOLOGY.md) ve [ADR‑0008](../docs/adr/0008-benchmark-architecture.md) belgelerine bakın.
