# MESA Benchmark

MESA Benchmark, bellek/retrieval sistemlerini aynı Top‑K ve aynı cevap üreticisiyle karşılaştıran, yeniden başlatılabilir bir benchmark runner’ıdır. MESA, Mem0, Zep ve Letta adaptörlerini destekler.

## Ölçüm sözleşmesi

Her soru iki ayrı hatta ölçülür:

- Retrieval: `Hit@1/3/5`, MRR, nDCG@5 ve yalnızca retrieval latency. `expected_context_ids` olmayan BEAM sorularında bu metrikler `N/A` olur.
- Full‑QA: bütün sistemlerin getirdiği Top‑5 bağlam aynı Ollama generator’a verilir; normalized EM, token F1, semantic judge, generation latency ve token kullanımı ayrı kaydedilir.

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
export BENCHMARK_JUDGE_MODEL='qweb:8b'
```

Tek URL’den `MESA_OLLAMA_URL`, `OLLAMA_HOST` ve `OPENAI_BASE_URL` türetilir. Model etiketi `/api/tags` sonucuyla tam eşleşmelidir.

```bash
mesa-benchmark config-check --config mesa-benchmark/config_mini_mesa.yaml
mesa-benchmark dataset-check --config mesa-benchmark/config_mini_mesa.yaml
mesa-benchmark ollama-preflight --config mesa-benchmark/config_mini_mesa.yaml
```

Preflight model etiketlerini ve şemalı JSON chat yanıtını doğrular.

## Önerilen çalışma sırası

```bash
# 1. MESA mini
mesa-benchmark run --config mesa-benchmark/config_mini_mesa.yaml

# 2. Mem0 mini
mesa-benchmark run --config mesa-benchmark/config_mini_mem0.yaml

# 3. Comprehensive
mesa-benchmark run --config mesa-benchmark/config.yaml

# 4. Aynı seed'lerde baseline ve eşlenmiş soru karşılaştırması
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --baseline-config mesa-benchmark/config_mem0.yaml \
  --seeds 42,43,44,45,46 \
  --output results/reproducibility_report.json
```

Her seed ayrı dizin, manifest ve state dosyası kullanır. Resume yalnızca effective config ve dataset SHA‑256 değerleri aynıysa yapılır; soru anahtarları append-only JSONL’den yeniden kurulur. State dosyası yalnız scenario checkpoint’lerinde yazılır.

## Veri kaynakları

- `comprehensive_200` ve türevleri sentetiktir; yalnızca iç regresyon için kullanılır.
- BEAM ve LoCoMo revision, checksum, lisans ve metric kısıtları `datasets/SOURCES.json` içinde sabittir.
- LoCoMo indirme/dönüştürme: `python mesa-benchmark/scripts/download_locomo.py`. Lisansı CC‑BY‑NC‑4.0 olduğundan ticari kullanım ayrıca değerlendirilmelidir.

## Docker

Build context repository kökü olmalıdır:

```bash
docker build -f mesa-benchmark/Dockerfile -t mesa-benchmark .
docker run --rm --env-file mesa-benchmark/.env \
  mesa-benchmark --config mesa-benchmark/config_mini_mesa.yaml
```

Image semantic embedding modelini build sırasında önbelleğe alır.

## Test

```bash
PYTHONPATH=mesa-benchmark python -m pytest mesa-benchmark/tests -q
ruff check mesa-benchmark/mesa_benchmark mesa-benchmark/tests
```

P95/P99 yalnız en az 20 latency gözlemi varsa raporlanır; küçük mini koşumlarda değer `N/A` olur. Bu, maksimum gecikmenin percentile gibi sunulmasını engeller.

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
