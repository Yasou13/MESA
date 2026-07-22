# MESA Benchmark Kullanım Kılavuzu

Canonical suite/config/manifest dosyaları kurulu wheel içindeki
`resource://` URI’leriyle, büyük senkronize datasetler `data://` URI’leriyle
adreslenir. `MESA_BENCHMARK_DATA_DIR` ve `MESA_BENCHMARK_RESULTS_DIR` ile
varsayılan kökler değiştirilebilir. Eski `mesa-benchmark/config_*.yaml` adları
yalnız geriye uyumlu alias olarak desteklenir.

## CLI

Kurulumdan sonra tek config ve suite komutları birlikte kullanılabilir:

```bash
mesa-benchmark config-check --config CONFIG
mesa-benchmark dataset-check --config CONFIG --profile internal|publishable
mesa-benchmark ollama-preflight --config CONFIG
mesa-benchmark run --config CONFIG [--seed N] [--max-scenarios N]
mesa-benchmark dataset-sync --suite smoke|release|research
mesa-benchmark suite-check --suite smoke|release|research
mesa-benchmark run-suite --suite SUITE --results-root results
mesa-benchmark verify-results --bundle BUNDLE
```

`config-check` strict Pydantic config şemasını ve ağsız canlı Full-QA
sözleşmesini; `dataset-check` schema v2, manifest, checksum, lisans, ID/evidence,
duplicate budget ve graph-fairness kapılarını; `suite-check` suite kapsamını;
`ollama-preflight` ise URL, tam model etiketi ve structured JSON chat davranışını
doğrular. Eksik environment/model/credential kontrollü non-zero hata üretir.

Offline kabul akışı:

```bash
mesa-benchmark dataset-sync --suite smoke
mesa-benchmark run-suite --suite smoke --results-root results
```

Release koşumundan önce `BENCHMARK_OLLAMA_URL`, exact
`BENCHMARK_GENERATOR_MODEL`, farklı `BENCHMARK_JUDGE_MODEL`,
`BENCHMARK_EMBEDDING_MODEL`, Letta endpoint/model değişkenleri ve en az 100
örnekli `BENCHMARK_JUDGE_CALIBRATION_PATH` sağlanmalıdır. Secret değerleri
config veya bundle içine yazılmaz.

`research` suite LoCoMo yanında BEAM 500K ve 1M nightly scale tracklerini
içerir; BEAM 512/64 ablation ve MemoryAgentBench Recsys `Recall@5` ikincil
trackleri ayrı gruplardır. 10M capacity track’i varsayılan sync sırasında atlanır; yalnız
`MESA_BENCHMARK_ENABLE_10M=1` ile üretilir ve
`config_beam_10m_capacity.yaml` üzerinden opt-in çalıştırılır. Bu sonuç hiçbir
koşulda publishable kalite skoruna katılmaz.

## Config şeması

```yaml
suite_name: "MESA comprehensive"
iterations: 1
seed: 42

dataset:
  name: comprehensive_200
  version: v2
  path: resource://fixtures/legacy/comprehensive_200_dataset.json
  manifest_path: resource://manifests/internal/comprehensive-v2.json
  isolation: scenario
  ingest_mode: batch
  noise_ratio: 0.0

client:
  name: mesa_client
  adapter_class: mesa_benchmark.clients.mesa_client.MesaClientAdapter
  timeout_ms: 30000
  parameters:
    enable_multi_hop: true

evaluation:
  metrics: [hit_at_k, mrr, latency, efficiency]
  llm_judge_model: null
  multi_judge_models: []
  enable_agreement: true
  judge_timeout_s: 120
  judge_ensemble_size: 3
  judge_quorum: 2
  judge_max_concurrency: 3

generation:
  enabled: true
  model: null
  timeout_s: 120
  temperature: 0

runtime:
  top_k: 5
  context_token_budget: 4096
  track: full-cognitive
  ollama_url: null
  require_independent_judge: true
```

Environment config değerlerini ezebilir:

```bash
BENCHMARK_OLLAMA_URL=http://host:11434
BENCHMARK_GENERATOR_MODEL=qweb:8b
BENCHMARK_JUDGE_MODEL=independent-judge:8b
BENCHMARK_JUDGE_MODELS=judge-a:tag,judge-b:tag
BENCHMARK_EMBEDDING_MODEL=nomic-embed-text:latest
```

`runtime.top_k` bütün adapter parametrelerinin üzerine yazılır. Config’teki `null` model değerleri bilinçli placeholder’dır. `generation.enabled=true` iken generator model ve Ollama URL’si; agreement veya independent judge isteniyorsa uygun judge modeli `config-check` sırasında zorunludur. Bu kontrol ağ erişimi gerektirmez.

## Sonuç dosyaları

`results/<client>/<dataset>_<version>_seed<seed>/` altında:

- `manifest_<run_id>.json`: config/dataset/manifest hashleri, exact modeller,
  protokol, lisans, chunking ve Top‑K.
- `results_<run_id>.jsonl`: JSONL v3 soru seviyesinde input/chunk hashleri,
  retrieval, Full‑QA, latency/token/storage ve altyapı alanları.
- `report_<run_id>.md`: özet metrik ve validity.
- `.state.json`: hash kontrollü resume ve scenario-level checkpoint. Tamamlanan soru anahtarları dayanıklı JSONL’den resume sırasında yeniden kurulur.

Eski hash içermeyen state otomatik resume edilmez. Mevcut raw sonuçlar silinmez veya üzerine yazılmaz.

P95/P99 yalnızca en az 20 retrieval-latency gözlemi için nearest-rank yöntemiyle hesaplanır; daha küçük sample’larda rapor `N/A` gösterir. Multi-seed JSON özetinde `N/A` metrikler sıfır olarak ortalanmaz; her metrik için kullanılan/dışlanan seed’ler yazılır.

Rapor doğrulukları üç ayrı satırda verir: tüm primary evaluator sonuçlarının birleşik micro-average değeri, exact-match/regex deterministic doğruluğu ve LLM judge/multi-model judge semantic doğruluğu. Semantic judge sorusu yoksa ilgili satır `N/A` olur.

## Multi-seed ve baseline

```bash
python scripts/reproduce_benchmark.py \
  --config resource://configs/legacy/default.yaml \
  --baseline-config resource://configs/legacy/mem0.yaml \
  --seeds 42,43,44,45,46 \
  --results-root results \
  --output results/reproducibility_report.json
```

Her seed gerçek runner çağrısıdır. Özet mean/std/SE/%95 CI içerir. Baseline karşılaştırması aynı seed/soru anahtarlarını eşler. Bir seed bile başarısızsa rapor `valid=false` ve process exit code `1` olur.

## LoCoMo

```bash
python mesa-benchmark/scripts/download_locomo.py
mesa-benchmark dataset-check --config resource://configs/research/locomo.yaml
```

Downloader typed manifestteki tam resmi revision’ı kabul eder ve SHA‑256
uyuşmazlığında durur. LoCoMo CC‑BY‑NC‑4.0 lisanslıdır ve yalnız `research`
suite’indedir.

## Yeni adapter

Adapter `AbstractBenchmarkClient` uygulamalıdır:

- `initialize(parameters)`
- `clear_memory()`
- `add_memory(context)`
- `answer(question) -> BenchmarkResponse`
- `close()`

Varsayılan `add_memories()` sıralı ingest yapar. Graph sistemi batch içindeki bütün node’ları edge’lerden önce oluşturmak için override etmelidir. `BenchmarkResponse.retrieved_contexts` sıralı ID/text/rank/score taşır; `retrieval_latency_ms`, `generation_latency_ms` ve token kullanımı ayrı alanlardır. Eski `answer_text`, `retrieved_context_ids` ve `latency_ms` alanları uyumluluk için korunur.

Provider hataları loglanıp boş/sahte başarıya çevrilmemelidir. Query limiti adapter içinde de Top‑5 olmalı; runner ayrıca sözleşmeyi zorlar.

## Uzak Ollama çalıştırma sırası

```bash
export BENCHMARK_OLLAMA_URL='http://REMOTE:11434'
export BENCHMARK_GENERATOR_MODEL='qweb:8b'
export BENCHMARK_JUDGE_MODEL='independent-judge:8b'

mesa-benchmark ollama-preflight -c resource://configs/legacy/mini_mesa.yaml
mesa-benchmark run -c resource://configs/legacy/mini_mesa.yaml
mesa-benchmark run -c resource://configs/legacy/mini_mem0.yaml
mesa-benchmark run -c resource://configs/legacy/default.yaml
python scripts/reproduce_benchmark.py \
  --config resource://configs/legacy/default.yaml \
  --baseline-config resource://configs/legacy/mem0.yaml \
  --seeds 42,43,44,45,46
```

Varsayılan config `require_independent_judge: true` olduğu için generator ile aynı judge etiketi `config-check` tarafından reddedilir. Yalnız iç regresyon için aynı `qweb:8b` kullanılacaksa bu değeri `false` yapın; rapor bilinçli olarak `provisional/self-judged` olur. `publishable` için `BENCHMARK_JUDGE_MODEL` generator’dan farklı bağımsız bir model etiketi olmalıdır.
