# MESA Benchmark Suite — Kullanım Kılavuzu

Bu kılavuz, benchmark altyapısının konfigürasyonu, veri setlerinin nasıl ekleneceği, yeni adaptörlerin (client) nasıl yazılacağı, değerlendirme modüllerinin detayları, reproducibility pipeline'ı ve yayın altyapısını içerir.

---

## İçindekiler

1. [Konfigürasyon Yapısı](#1-konfigürasyon-yapısı-configyaml)
2. [Veri Seti Mimarisi](#2-veri-seti-mimarisi)
3. [Desteklenen Bellek Sistemleri](#3-desteklenen-bellek-sistemleri-client-adapters)
4. [Değerlendirme Pipeline'ı](#4-değerlendirme-pipelineı-evaluators)
5. [Multi-Seed Reproducibility](#5-multi-seed-reproducibility)
6. [Harici Benchmark Entegrasyonu (LoCoMo)](#6-harici-benchmark-entegrasyonu-locomo)
7. [İzolasyon ve Kesintiden Devam Etme](#7-izolasyon-ve-kesintiden-devam-etme-resilience)
8. [Raporların Okunması](#8-raporların-okunması)
9. [HuggingFace Yayın Altyapısı](#9-huggingface-yayın-altyapısı)
10. [Docker ile Reproducible Çalıştırma](#10-docker-ile-reproducible-çalıştırma)
11. [Yeni Adaptör Yazma Rehberi](#11-yeni-bellek-istemcisi-client-adapter-eklemek)

---

## 1. Konfigürasyon Yapısı (`config.yaml`)

Benchmark yürütme motoru `config.yaml` dosyasından beslenir. Bu dosyayı değiştirerek test edeceğiniz sistemi, çalıştırılacak iterasyon sayısını, değerlendirme yöntemlerini ve istatistikleri belirleyebilirsiniz.

```yaml
suite_name: "MESA v0.6.0 Comprehensive Benchmark"
iterations: 1          # Multi-seed istatistik için en az 5 önerilir
seed: 42

dataset:
  name: "comprehensive_200"
  version: "v2"
  path: "mesa-benchmark/mesa_benchmark/datasets/comprehensive_200_dataset.json"
  noise_ratio: 0.0

client:
  name: "mesa_client"
  adapter_class: "mesa_benchmark.clients.mesa_client.MesaClientAdapter"
  timeout_ms: 30000
  parameters:
    verbose: false
    enable_multi_hop: true
    enable_rerank: false
    top_n: 5

evaluation:
  metrics:
    - "hit_at_k"
    - "mrr"
    - "latency"
    - "efficiency"
  llm_judge_model: "openai/qwen3:8b"        # Tek model LLM-as-a-Judge
  multi_judge_models:                         # Çoklu model bağımsız değerlendirme
    - "openai/qwen3:8b"
  enable_agreement: true                      # Keyword vs LLM-Judge uyum raporu
```

### Konfigürasyon Alanları

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `suite_name` | string | — | Benchmark raporunda görünecek isim |
| `iterations` | int | `5` | Kaç kez çalıştırılacağı (varyans hesabı için ≥5) |
| `seed` | int | `42` | Tekrarlanabilirlik için rastgele tohum |
| `dataset.path` | string | — | JSON veri seti dosyasının yolu (proje kökünden) |
| `dataset.noise_ratio` | float | `0.0` | Gürültü oranı (0.0 – 1.0) |
| `client.adapter_class` | string | — | Python modül yolu (tam nitelikli) |
| `client.timeout_ms` | int | `10000` | API zaman aşımı (ms) |
| `client.parameters` | dict | `{}` | Adaptöre özel parametreler |
| `evaluation.llm_judge_model` | string | `null` | Tek model LLM judge (null = devre dışı) |
| `evaluation.multi_judge_models` | list | `[]` | Çoklu model bağımsız judge listesi |
| `evaluation.enable_agreement` | bool | `false` | Keyword ↔ LLM-Judge uyum raporu |

### MESA Client Parametreleri

| Parametre | Tip | Varsayılan | Açıklama |
|-----------|-----|-----------|----------|
| `enable_multi_hop` | bool | `true` | KùzuDB çizge geçişini etkinleştir |
| `enable_rerank` | bool | `false` | CrossEncoder reranking |
| `reranker_model` | string | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker model |
| `top_n` | int | `5` | Getirilecek sonuç sayısı |
| `timeout_s` | float | `30.0` | Sorgu zaman aşımı (saniye) |

### Hazır Config Dosyaları

| Config | Sistem | Veri Seti | Açıklama |
|--------|--------|-----------|----------|
| `config.yaml` | MESA | comprehensive_200 | Ana benchmark (200 senaryo) |
| `config_beam.yaml` | MESA | beam | BEAM karşılaştırma (400 soru) |
| `config_contradiction.yaml` | MESA | contradiction_200 | Çelişki çözümü (200 senaryo) |
| `config_multi_hop.yaml` | MESA | multihop_only | Yalnızca multi-hop (60 senaryo) |
| `config_reranking.yaml` | MESA | comprehensive_200 | CrossEncoder reranking etkin |
| `config_mem0.yaml` | Mem0 | comprehensive_200 | Mem0 baseline |
| `config_zep.yaml` | Zep | comprehensive_200 | Zep baseline |
| `config_letta.yaml` | Letta | comprehensive_200 | Letta/MemGPT baseline |
| `config_mini_mesa.yaml` | MESA | mini (2 senaryo) | Hızlı doğrulama |
| `config_mini_mem0.yaml` | Mem0 | mini (2 senaryo) | Hızlı doğrulama |

---

## 2. Veri Seti Mimarisi

Sistem, soruları ve bağlamları JSON dosyaları üzerinden okur. 200 senaryoluk kapsamlı dataset dört zorluk katmanından oluşur:

| Katman | Oran | Senaryo Sayısı | Test Ettiği Şey |
|--------|------|---------------|------------------|
| **Single-Hop** | %40 | 80 | Tek bellek düğümünden doğrudan bilgi getirme |
| **Multi-Hop** | %30 | 60 | 2+ bellek düğümü arasında çizge geçişi |
| **Hard-Negative** | %15 | 30 | Eski bilgi vs. güncel bilgi çelişki çözümü |
| **Out-of-Domain** | %15 | 30 | İlgisiz bilgiyi karantinaya alma |

### Veri Seti Formatı

Her senaryo şu yapıdadır:

```json
[
  {
    "id": "multi_hop_scenario_0",
    "name": "Multi-Hop Graph Traversal #0",
    "description": "Tests multi-hop connection across two entity nodes.",
    "contexts": [
      {
        "id": "multi_0_ctx1",
        "text": "Dr. Elena Vance is the lead investigator for Project Omega-0.",
        "metadata": {
          "tier": "multi_hop",
          "relations": [{"target": "Project Omega-0", "type": "LEADS"}]
        }
      },
      {
        "id": "multi_0_ctx2",
        "text": "Project Omega-0 has relocated its primary R&D headquarters to Zurich.",
        "metadata": {
          "tier": "multi_hop",
          "relations": [{"target": "Zurich", "type": "LOCATED_IN"}]
        }
      }
    ],
    "questions": [
      {
        "id": "multi_0_q",
        "query": "In which city is the primary R&D headquarters of the project led by Dr. Elena Vance?",
        "ground_truth": "Zurich",
        "expected_context_ids": ["multi_0_ctx1", "multi_0_ctx2"],
        "evaluation_strategy": "llm_judge"
      }
    ]
  }
]
```

### Mevcut Veri Setleri

| Veri Seti | Konum | Senaryolar | Sorular |
|-----------|-------|------------|---------|
| `comprehensive_200_dataset.json` | `mesa_benchmark/datasets/` | 200 | 200 |
| `mini_dataset.json` | `mesa_benchmark/datasets/` | 2 | 2 |
| `stress_dataset.json` | `mesa_benchmark/datasets/` | 100 | 100 |
| `beam/dataset.json` | `datasets/` | 20 | 400 |
| `contradiction_200.json` | `datasets/` | 200 | 200 |
| `comprehensive_multihop_only.json` | `datasets/` | 60 | 60 |

### Değerlendirme Stratejileri (`evaluation_strategy`)

| Strateji | Maliyet | Açıklama |
|----------|---------|----------|
| `"exact_match"` | Ücretsiz | Alt dize (substring) eşleşmesi. Hızlı, basit. |
| `"llm_judge"` | API maliyeti | Tek LLM model anlamsal değerlendirme. `OPENAI_BASE_URL` veya API key gerekir. |
| `"multi_model_judge"` | Yüksek maliyeti | 2-3 farklı model ile bağımsız değerlendirme + majority voting. |

### Yeni Veri Seti Oluşturma

200 senaryoluk veri setini yeniden oluşturmak için:

```bash
cd mesa-benchmark
python scripts/generate_comprehensive_dataset.py
```

Stress test veri seti oluşturmak için:

```bash
python scripts/generate_stress_dataset.py
```

---

## 3. Desteklenen Bellek Sistemleri (Client Adapters)

| Sistem | Adapter Sınıfı | Config | Kurulum |
|--------|----------------|--------|---------|
| **MESA** | `MesaClientAdapter` | `config.yaml` | Yerleşik (kurulum gerekmez) |
| **Mem0** | `Mem0ClientAdapter` | `config_mem0.yaml` | `pip install mem0ai` |
| **Zep** | `ZepClientAdapter` | `config_zep.yaml` | `pip install zep-cloud` |
| **Letta/MemGPT** | `LettaClientAdapter` | `config_letta.yaml` | `pip install letta` |
| **BareRAG (Kontrol)** | `DummyClientAdapter` | — | Yerleşik |

### Rakip Benchmark Çalıştırma

Her rakip sistemi aynı veri setine karşı çalıştırabilirsiniz (proje kök dizininden):

```bash
# MESA (varsayılan)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42

# Mem0 baseline
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_mem0.yaml --seeds 42

# Zep baseline
export ZEP_API_KEY="your-zep-api-key"
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_zep.yaml --seeds 42

# Letta/MemGPT (önce Letta sunucusunu başlatın)
letta server &
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_letta.yaml --seeds 42
```

> **Not:** Tüm sistemler aynı `AbstractBenchmarkClient` interface'ini uyguladığı için, aynı veri seti, aynı evaluator ve aynı metriklerle değerlendirilir — **Apple-to-Apple** karşılaştırma garanti edilir.

---

## 4. Değerlendirme Pipeline'ı (Evaluators)

Benchmark v4'te üç kademeli bir değerlendirme sistemi bulunur:

### 4.1 Keyword/Exact Match (Birincil — Ücretsiz)

Her soru için birincil evaluator çalışır. `evaluation_strategy` alanına göre seçilir.

### 4.2 LLM-as-a-Judge (İkincil)

Config'te `enable_agreement: true` ise, birincil evaluator'a ek olarak LLM Judge otomatik çalıştırılır. İki evaluator arasındaki uyum hesaplanır.

```yaml
evaluation:
  llm_judge_model: "openai/qwen3:8b"
  enable_agreement: true
```

LLM Judge, Qwen3 modelleri için otomatik olarak `/no_think` modunu kullanarak JSON çıktısı alır. Yerel Ollama sunucusu kullanıldığında `OPENAI_BASE_URL` env var'ı üzerinden yönlendirme yapılır.

### 4.3 Multi-Model Judge (Bağımsız Değerlendirme)

Self-grading bias'ı engellemek için 2-3 farklı LLM model kullanılır:

```yaml
evaluation:
  multi_judge_models:
    - "openai/qwen3:8b"
    - "gpt-4o-mini"
```

**Nasıl çalışır:**
1. Her soru aynı prompt ile tüm modellere gönderilir
2. Her model bağımsız olarak `{is_correct, score, reasoning}` döner
3. **Majority voting** ile final karar verilir
4. Modeller arası **pairwise agreement** oranı hesaplanır
5. Tüm model detayları metadata'da saklanır

### 4.4 Agreement Rate (Metodolojik Doğrulama)

Keyword evaluator ile LLM-Judge arasındaki uyum otomatik hesaplanır:

| Metrik | Açıklama |
|--------|----------|
| **Agreement Rate (%)** | İki evaluator'ın aynı karara vardığı soru oranı |
| **Cohen's Kappa** | Şans uyumunu çıkaran istatistiksel uyum katsayısı (-1.0 → 1.0) |
| **Contingency Table** | Detaylı çapraz tablo (both correct, only A correct, vs.) |

Agreement Rate ≥ %85 ise, keyword matching'in LLM-Judge ile uyumlu, güvenilir bir proxy olduğu kanıtlanır.

---

## 5. Multi-Seed Reproducibility

LLM'ler stokastik olduğundan, tek bir çalıştırma güvenilir sonuç vermez.

### Otomatik Çalıştırma (Önerilen)

```bash
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --seeds 42,43,44,45,46 \
  --output reproducibility_report.json
```

### Çıktı Formatı

```json
{
  "seeds_run": [42, 43, 44, 45, 46],
  "seeds_completed": 5,
  "runs": [
    {"seed": 42, "status": "success"},
    {"seed": 43, "status": "success"}
  ]
}
```

### Hızlı Test (Senaryo Limiti)

Tüm 200 senaryoyu çalıştırmadan önce test etmek isterseniz:

```bash
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --seeds 42 \
  --max-scenarios 10
```

---

## 6. Harici Benchmark Entegrasyonu (LoCoMo)

MESA'yı uluslararası tanınan LoCoMo benchmark'ına karşı çalıştırmak, sonuçlarınızın karşılaştırılabilir olmasını sağlar.

### Adım 1: LoCoMo Veri Setini İndirin

```bash
cd mesa-benchmark
python scripts/download_locomo.py
```

Bu komut:
- HuggingFace'ten `passing2961/LoCoMo` dataset'ini indirir
- MESA `BenchmarkScenario` formatına dönüştürür
- `datasets/locomo/dataset.json` olarak kaydeder

### Adım 2: LoCoMo'ya Karşı Çalıştırın

```bash
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_locomo.yaml --seeds 42
```

---

## 7. İzolasyon ve Kesintiden Devam Etme (Resilience)

- **Tam İzolasyon:** Her iterasyon öncesinde `clear_memory()` çağrılır. Bu çağrı başarısız olursa benchmark **derhal durur** (`MemoryPurgeError`).
- **Exponential Backoff:** API limit aşımlarında katlanarak artan bekleme süreleriyle (1s, 2s, 4s...) en fazla 3 kez tekrar dener.
- **Kaldığı Yerden Devam:** Tüm ilerleme `.state.json` dosyasında tutulur. Sistemi tekrar çalıştırdığınızda otomatik olarak son kalınan noktadan devam eder.
- **Noise Parity:** Kaldığı yerden devam ederken, atlanmış senaryoların bağlamları veritabanına geri yüklenir.

---

## 8. Raporların Okunması

Test tamamlandıktan sonra `results/{client}/{dataset}_seed{N}/` altında dosyalar oluşur:

### Rapor Bölümleri

| Bölüm | Açıklama |
|-------|----------|
| **Accuracy & Reliability** | Doğruluk oranı, toplam soru/doğru sayısı |
| **Methodological Verification** | Agreement rate, Cohen's Kappa, contingency table |
| **Speed & Latency** | Ortalama, P95, P99 latency |
| **Retrieval Performance** | Hit@1, Hit@3, Hit@5, MRR, nDCG@5 |
| **Token Efficiency** | Doğru cevap başına token maliyeti |
| **Root-Cause Diagnostics** | Failure attribution breakdown + internal latency breakdown |

---

## 9. HuggingFace Yayın Altyapısı

Benchmark veri setinizi HuggingFace Hub'da yayımlayabilirsiniz:

```bash
cd mesa-benchmark

# HuggingFace token'ınızı ayarlayın
export HF_TOKEN="hf_your_token_here"

# Varsayılan veri setini yayımla
python scripts/publish_to_hf.py \
  --dataset-path mesa_benchmark/datasets/comprehensive_200_dataset.json \
  --repo-id your-org/mesa-benchmark \
  --version 2.0
```

---

## 10. Docker ile Reproducible Çalıştırma

```bash
cd mesa-benchmark

# Image oluştur
docker build -t mesa-benchmark .

# Varsayılan benchmark çalıştır
docker run --env-file .env mesa-benchmark

# Belirli config ile çalıştır
docker run --env-file .env mesa-benchmark --config config_mem0.yaml
```

---

## 11. Yeni Bellek İstemcisi (Client Adapter) Eklemek

`AbstractBenchmarkClient` sınıfını uygulamanız yeterlidir:

### Adım 1: Adapter Dosyası Oluşturun

`mesa_benchmark/clients/` altına yeni bir dosya açın (örn: `my_system_client.py`):

```python
import time
from typing import Any, Dict

from mesa_benchmark.clients.base import AbstractBenchmarkClient, BenchmarkResponse
from mesa_benchmark.datasets.schemas import BenchmarkQuestion, MemoryContext

try:
    from my_system import MySystemClient
    MY_SYSTEM_AVAILABLE = True
except ImportError:
    MySystemClient = None
    MY_SYSTEM_AVAILABLE = False


class MySystemAdapter(AbstractBenchmarkClient):

    def __init__(self) -> None:
        self.client = None

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if not MY_SYSTEM_AVAILABLE:
            raise ImportError("my_system library is not installed.")
        self.client = MySystemClient(api_key=config_params.get("api_key"))

    def clear_memory(self) -> None:
        # ⚠️ KRİTİK: Tüm belleği SIFIRLAYIN — izolasyon için zorunlu
        self.client.reset()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start = time.perf_counter()
        self.client.add(text=context.text, metadata={"id": context.id})
        return {"latency_ms": (time.perf_counter() - start) * 1000}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start = time.perf_counter()
        results = self.client.search(question.query, limit=5)
        answer_text = "\n".join([r.text for r in results])
        retrieved_ids = [r.metadata.get("id", "") for r in results]

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    def close(self) -> None:
        self.client = None
```

### Adım 2: Config Dosyası Oluşturun

```yaml
suite_name: "MySystem Baseline"
iterations: 1
seed: 42

dataset:
  name: "comprehensive_200"
  version: "v2"
  path: "mesa-benchmark/mesa_benchmark/datasets/comprehensive_200_dataset.json"
  noise_ratio: 0.0

client:
  name: "my_system"
  adapter_class: "mesa_benchmark.clients.my_system_client.MySystemAdapter"
  timeout_ms: 10000
  parameters:
    api_key: "${MY_SYSTEM_API_KEY}"

evaluation:
  metrics: ["hit_at_k", "mrr", "latency"]
  llm_judge_model: "openai/qwen3:8b"
  enable_agreement: true
```

### Adım 3: Çalıştırın

```bash
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_my_system.yaml --seeds 42
```

> **Önemli:** `clear_memory()` metodunun tüm verileri tamamen sildiğinden emin olun. Bu metot başarısız olursa benchmark hemen durur — çapraz veri kirliliği hiçbir koşulda tolere edilmez.

---

## Hızlı Referans — Sık Kullanılan Komutlar

```bash
# Hızlı doğrulama (mini dataset)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_mini_mesa.yaml --seeds 42

# Ana benchmark (200 senaryo)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42

# Reproducibility raporu (5 seed)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42,43,44,45,46

# Senaryo limiti ile hızlı test
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42 --max-scenarios 10

# Docker ile çalıştır
docker build -t mesa-benchmark .
docker run --env-file .env mesa-benchmark
```
