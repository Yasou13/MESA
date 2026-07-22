# MESA Benchmark Kullanım Kılavuzu

## CLI

Kurulumdan sonra dört ana komut vardır:

```bash
mesa-benchmark config-check --config CONFIG
mesa-benchmark dataset-check --config CONFIG
mesa-benchmark ollama-preflight --config CONFIG
mesa-benchmark run --config CONFIG [--seed N] [--max-scenarios N]
```

`config-check` strict Pydantic config şemasını ve ağsız canlı Full-QA sözleşmesini; `dataset-check` kimlik, expected context ve graph relation bütünlüğünü; `ollama-preflight` ise URL, tam model etiketi ve structured JSON chat davranışını doğrular.

## Config şeması

```yaml
suite_name: "MESA comprehensive"
iterations: 1
seed: 42

dataset:
  name: comprehensive_200
  version: v2
  path: mesa-benchmark/mesa_benchmark/datasets/comprehensive_200_dataset.json
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

- `manifest_<run_id>.json`: effective config/dataset hash, seed, model ve Top‑K.
- `results_<run_id>.jsonl`: soru seviyesinde retrieval, Full‑QA ve altyapı alanları.
- `report_<run_id>.md`: özet metrik ve validity.
- `.state.json`: hash kontrollü resume ve scenario-level checkpoint. Tamamlanan soru anahtarları dayanıklı JSONL’den resume sırasında yeniden kurulur.

Eski hash içermeyen state otomatik resume edilmez. Mevcut raw sonuçlar silinmez veya üzerine yazılmaz.

P95/P99 yalnızca en az 20 retrieval-latency gözlemi için nearest-rank yöntemiyle hesaplanır; daha küçük sample’larda rapor `N/A` gösterir. Multi-seed JSON özetinde `N/A` metrikler sıfır olarak ortalanmaz; her metrik için kullanılan/dışlanan seed’ler yazılır.

Rapor doğrulukları üç ayrı satırda verir: tüm primary evaluator sonuçlarının birleşik micro-average değeri, exact-match/regex deterministic doğruluğu ve LLM judge/multi-model judge semantic doğruluğu. Semantic judge sorusu yoksa ilgili satır `N/A` olur.

## Multi-seed ve baseline

```bash
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --baseline-config mesa-benchmark/config_mem0.yaml \
  --seeds 42,43,44,45,46 \
  --results-root results \
  --output results/reproducibility_report.json
```

Her seed gerçek runner çağrısıdır. Özet mean/std/SE/%95 CI içerir. Baseline karşılaştırması aynı seed/soru anahtarlarını eşler. Bir seed bile başarısızsa rapor `valid=false` ve process exit code `1` olur.

## LoCoMo

```bash
python mesa-benchmark/scripts/download_locomo.py
mesa-benchmark dataset-check --config mesa-benchmark/config_locomo.yaml
```

Downloader yalnızca `datasets/SOURCES.json` içindeki tam official revision’ı kabul eder ve SHA‑256 uyuşmazlığında durur. LoCoMo CC‑BY‑NC‑4.0 lisanslıdır.

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

mesa-benchmark ollama-preflight -c mesa-benchmark/config_mini_mesa.yaml
mesa-benchmark run -c mesa-benchmark/config_mini_mesa.yaml
mesa-benchmark run -c mesa-benchmark/config_mini_mem0.yaml
mesa-benchmark run -c mesa-benchmark/config.yaml
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --baseline-config mesa-benchmark/config_mem0.yaml \
  --seeds 42,43,44,45,46
```

Varsayılan config `require_independent_judge: true` olduğu için generator ile aynı judge etiketi `config-check` tarafından reddedilir. Yalnız iç regresyon için aynı `qweb:8b` kullanılacaksa bu değeri `false` yapın; rapor bilinçli olarak `provisional/self-judged` olur. `publishable` için `BENCHMARK_JUDGE_MODEL` generator’dan farklı bağımsız bir model etiketi olmalıdır.
