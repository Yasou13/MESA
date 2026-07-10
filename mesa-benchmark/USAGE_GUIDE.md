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
suite_name: "MESA v0.5.1 Comprehensive Benchmark"
iterations: 5          # Multi-seed istatistik için en az 5 önerilir
seed: 42

dataset:
  name: "comprehensive_200"
  version: "v2"
  path: "mesa_benchmark/datasets/comprehensive_200_dataset.json"
  noise_ratio: 0.0

client:
  name: "mesa_client"
  adapter_class: "mesa_benchmark.clients.mesa_client.MesaClientAdapter"
  timeout_ms: 10000
  parameters:
    verbose: false

evaluation:
  metrics:
    - "hit_at_k"
    - "mrr"
    - "latency"
    - "efficiency"
  llm_judge_model: "gpt-4o-mini"          # Tek model LLM-as-a-Judge
  multi_judge_models:                       # Çoklu model bağımsız değerlendirme
    - "gpt-4o-mini"
    - "claude-sonnet-4-20250514"
  enable_agreement: true                    # Keyword vs LLM-Judge uyum raporu
```

### Konfigürasyon Alanları

| Alan | Tip | Varsayılan | Açıklama |
|------|-----|-----------|----------|
| `suite_name` | string | — | Benchmark raporunda görünecek isim |
| `iterations` | int | `5` | Kaç kez çalıştırılacağı (varyans hesabı için ≥5) |
| `seed` | int | `42` | Tekrarlanabilirlik için rastgele tohum |
| `dataset.path` | string | — | JSON veri seti dosyasının yolu |
| `dataset.noise_ratio` | float | `0.0` | Gürültü oranı (0.0 – 1.0) |
| `client.adapter_class` | string | — | Python modül yolu (tam nitelikli) |
| `client.timeout_ms` | int | `10000` | API zaman aşımı (ms) |
| `evaluation.llm_judge_model` | string | `null` | Tek model LLM judge (null = devre dışı) |
| `evaluation.multi_judge_models` | list | `[]` | Çoklu model bağımsız judge listesi |
| `evaluation.enable_agreement` | bool | `false` | Keyword ↔ LLM-Judge uyum raporu |

### Hazır Config Dosyaları

| Config | Sistem | Açıklama |
|--------|--------|----------|
| `config.yaml` | MESA | Ana benchmark (200 senaryo, 5 iterasyon) |
| `config_locomo.yaml` | MESA | LoCoMo uluslararası benchmark |
| `config_zep.yaml` | Zep | Zep rakip baseline |
| `config_letta.yaml` | Letta/MemGPT | Letta rakip baseline |

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

### Değerlendirme Stratejileri (`evaluation_strategy`)

| Strateji | Maliyet | Açıklama |
|----------|---------|----------|
| `"exact_match"` | Ücretsiz | Alt dize (substring) eşleşmesi. Hızlı, basit. |
| `"llm_judge"` | API maliyeti | Tek LLM model (GPT-4o-mini) anlamsal değerlendirme. `OPENAI_API_KEY` gerekir. |
| `"multi_model_judge"` | Yüksek API maliyeti | 2-3 farklı model ile bağımsız değerlendirme + majority voting. Self-grading bias'ı engeller. |

### Yeni Veri Seti Oluşturma

200 senaryoluk veri setini yeniden oluşturmak için:

```bash
cd mesa-benchmark
python scripts/generate_comprehensive_dataset.py --seed 42 --output mesa_benchmark/datasets/comprehensive_200_dataset.json
```

---

## 3. Desteklenen Bellek Sistemleri (Client Adapters)

Benchmark suite şu anda 5 bellek sistemini destekler:

| Sistem | Adapter Sınıfı | Config | Kurulum |
|--------|----------------|--------|---------|
| **MESA** | `MesaClientAdapter` | `config.yaml` | Yerleşik (kurulum gerekmez) |
| **Mem0** | `Mem0ClientAdapter` | — | `pip install mem0ai` |
| **Zep** | `ZepClientAdapter` | `config_zep.yaml` | `pip install zep-cloud` |
| **Letta/MemGPT** | `LettaClientAdapter` | `config_letta.yaml` | `pip install letta` |
| **BareRAG (Kontrol)** | `DummyClientAdapter` | — | Yerleşik |

### Rakip Benchmark Çalıştırma

Her rakip sistemi aynı veri setine karşı çalıştırabilirsiniz:

```bash
# MESA (varsayılan)
python -m mesa_benchmark -c config.yaml

# Zep
export ZEP_API_KEY="your-zep-api-key"
python -m mesa_benchmark -c config_zep.yaml

# Letta/MemGPT (önce Letta sunucusunu başlatın)
letta server &
python -m mesa_benchmark -c config_letta.yaml

# Mem0
python -m mesa_benchmark -c config.yaml  # adapter_class'ı mem0_client olarak değiştirin
```

> **Not:** Tüm sistemler aynı `AbstractBenchmarkClient` interface'ini uyguladığı için, aynı veri seti, aynı evaluator ve aynı metriklerle değerlendirilir — **Apple-to-Apple** karşılaştırma garanti edilir.

---

## 4. Değerlendirme Pipeline'ı (Evaluators)

Benchmark v2'de üç kademeli bir değerlendirme sistemi bulunur:

### 4.1 Keyword/Exact Match (Birincil — Ücretsiz)

Her soru için birincil evaluator çalışır. `evaluation_strategy` alanına göre seçilir.

### 4.2 LLM-as-a-Judge (İkincil — API Maliyetli)

Config'te `enable_agreement: true` ise, birincil evaluator'a ek olarak LLM Judge otomatik çalıştırılır. İki evaluator arasındaki uyum hesaplanır.

```yaml
evaluation:
  llm_judge_model: "gpt-4o-mini"
  enable_agreement: true
```

### 4.3 Multi-Model Judge (Bağımsız Değerlendirme)

Self-grading bias'ı engellemek için 2-3 farklı LLM model kullanılır:

```yaml
evaluation:
  multi_judge_models:
    - "gpt-4o-mini"          # OpenAI
    - "claude-sonnet-4-20250514"   # Anthropic
```

**Nasıl çalışır:**
1. Her soru aynı prompt ile tüm modellere gönderilir
2. Her model bağımsız olarak `{is_correct, score, reasoning}` döner
3. **Majority voting** ile final karar verilir (2/3 doğru = doğru)
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

LLM'ler stokastik olduğundan, tek bir çalıştırma güvenilir sonuç vermez. Benchmark suite otomatik olarak multi-seed çalıştırma destekler.

### Otomatik Çalıştırma (Önerilen)

```bash
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --seeds 42,43,44,45,46 \
  --output reproducibility_report.json
```

### Çıktı Formatı

```json
{
  "seeds_run": [42, 43, 44, 45, 46],
  "seeds_completed": 5,
  "accuracy_statistics": {
    "mean": 90.50,
    "std": 1.04,
    "formatted_str": "90.50 ± 1.04",
    "ci_95": 1.36,
    "n": 5
  },
  "significance_test": {
    "t_stat": 3.42,
    "p_value_approx": 0.0012,
    "is_significant": true
  }
}
```

### Baseline Karşılaştırması (P-Value)

MESA'yı bir baseline'a karşı istatistiksel olarak test etmek için:

```bash
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --baseline-config mesa-benchmark/config_zep.yaml \
  --seeds 42,43,44,45,46
```

Bu komut Welch's t-test ile p-value hesaplar ve farkın istatistiksel olarak anlamlı olup olmadığını raporlar.

### Hızlı Test (Senaryo Limiti)

Tüm 200 senaryoyu çalıştırmadan önce test etmek isterseniz:

```bash
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --seeds 42,43 \
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
python -m mesa_benchmark -c config_locomo.yaml
```

### Adım 3: Sonuçları Raporlayın

LoCoMo sonuçları, mevcut akademik yayınlarla (Mem0'ın ECAI 2025 paper'ı gibi) doğrudan karşılaştırılabilir. Raporlama formatı:

> *"MESA, LoCoMo benchmark'ında X puan aldı. Mem0'ın yayımladığı Y puanıyla karşılaştırılabilir."*

---

## 7. İzolasyon ve Kesintiden Devam Etme (Resilience)

Benchmark aracı son derece katı bir hata kontrol sistemine sahiptir.

- **Tam İzolasyon:** Her iterasyon öncesinde `clear_memory()` çağrılır. Bu çağrı başarısız olursa benchmark **derhal durur** (`MemoryPurgeError`). Çapraz veri kirliliği asla tolere edilmez.
- **Exponential Backoff:** Herhangi bir API Limit aşımında (Rate Limit) araç çökmez, katlanarak artan bekleme süreleriyle (1s, 2s, 4s...) en fazla 3 kez tekrar dener.
- **Kaldığı Yerden Devam:** Sistem çalışırken bilgisayarınız kapanırsa panik yapmayın. Tüm ilerleme `state.json` dosyasında tutulur. Sistemi tekrar çalıştırdığınızda araç otomatik olarak son kalınan iterasyon ve senaryodan devam eder.
- **Noise Parity:** Kaldığı yerden devam ederken, atlanmış senaryoların bağlamları veritabanına geri yüklenir — böylece bellek durumu temiz başlangıçla aynı kalır.

Temiz bir test başlatmak istiyorsanız:

```bash
rm state.json results_*.jsonl
python -m mesa_benchmark -c config.yaml
```

---

## 8. Raporların Okunması

Test tamamlandıktan sonra `report_{RUN_ID}.md` dosyası oluşur. Rapor üç ana bölümden oluşur:

### 8.1 Accuracy & Reliability

| Metrik | Açıklama |
|--------|----------|
| **Total Questions** | Test edilen toplam soru sayısı |
| **Correct Answers** | Doğru cevap sayısı |
| **Accuracy** | Genel doğruluk oranı (%) |

### 8.2 Methodological Verification (Yeni)

`enable_agreement: true` ise raporda ek bir bölüm görünür:

| Metrik | Açıklama |
|--------|----------|
| **Agreement Rate** | Keyword ve LLM-Judge evaluator'ların uyum oranı |
| **Cohen's Kappa** | Şans uyumunu çıkaran istatistiksel katsayı |
| **Contingency Table** | Detaylı çapraz doğruluk tablosu |

### 8.3 Speed & Latency

| Metrik | Açıklama |
|--------|----------|
| **Average Latency** | Ortalama tepki süresi (ms) |
| **P95 Latency** | Sorguların %95'inin bitmesi için gereken süre |
| **P99 Latency** | En yavaş %1'lik sorguların süresi |

### 8.4 Retrieval Performance

| Metrik | Açıklama |
|--------|----------|
| **Hit@1** | Doğru bilgi 1. sırada geldi |
| **Hit@3** | Doğru bilgi ilk 3 sonuç içinde |
| **Hit@5** | Doğru bilgi ilk 5 sonuç içinde |
| **MRR** | Ortalama İlk Bulma Sırası (Mean Reciprocal Rank) |

---

## 9. HuggingFace Yayın Altyapısı

Benchmark veri setinizi ve sonuçlarınızı uluslararası görünürlük için HuggingFace Hub'da yayımlayabilirsiniz.

### Dataset Yayımlama

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

Bu komut otomatik olarak:
- HuggingFace dataset repository oluşturur
- Dataset card (README.md) ile YAML frontmatter üretir
- Lisans (Apache 2.0), citation bilgisi ve feature schema ekler
- Veri setini `test.json` olarak yükler

### LoCoMo Sonuçlarını Yayımlama

```bash
python scripts/publish_to_hf.py \
  --dataset-path datasets/locomo/dataset.json \
  --repo-id your-org/mesa-locomo-benchmark \
  --version 1.0
```

---

## 10. Docker ile Reproducible Çalıştırma

Tam reproducibility için Docker kullanılması önerilir. Dockerfile, pinned dependency versiyonları (`requirements-lock.txt`) kullanarak her makinede aynı ortamı garanti eder.

### Build ve Çalıştırma

```bash
cd mesa-benchmark

# Image oluştur
docker build -t mesa-benchmark .

# Varsayılan benchmark çalıştır
docker run --env-file .env mesa-benchmark

# LoCoMo benchmark çalıştır
docker run --env-file .env mesa-benchmark --config config_locomo.yaml

# Zep baseline çalıştır
docker run --env-file .env mesa-benchmark --config config_zep.yaml
```

### Reproducibility Kontrolü

Docker image'ı `requirements-lock.txt` kullandığı için:
- Her build aynı bağımlılık versiyonlarını yükler
- Farklı makinelerde aynı sonuçlar üretilir
- `seed` parametresi ile deterministik çalıştırma sağlanır

---

## 11. Yeni Bellek İstemcisi (Client Adapter) Eklemek

Rakip bir AI belleğini sisteme entegre edip MESA ile yarışmasını isterseniz tek yapmanız gereken `AbstractBenchmarkClient` sınıfını uygulamaktır.

### Adım 1: Adapter Dosyası Oluşturun

`mesa_benchmark/clients/` altına yeni bir dosya açın (örn: `my_system_client.py`):

```python
import time
from typing import Any, Dict

from mesa_benchmark.clients.base import AbstractBenchmarkClient, BenchmarkResponse
from mesa_benchmark.datasets.schemas import BenchmarkQuestion, MemoryContext

# Kütüphane yoksa güvenli hata ver
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
iterations: 5
seed: 42

dataset:
  name: "comprehensive_200"
  version: "v2"
  path: "mesa_benchmark/datasets/comprehensive_200_dataset.json"
  noise_ratio: 0.0

client:
  name: "my_system"
  adapter_class: "mesa_benchmark.clients.my_system_client.MySystemAdapter"
  timeout_ms: 10000
  parameters:
    api_key: "${MY_SYSTEM_API_KEY}"

evaluation:
  metrics: ["hit_at_k", "mrr", "latency"]
  llm_judge_model: "gpt-4o-mini"
  enable_agreement: true
```

### Adım 3: Çalıştırın

```bash
python -m mesa_benchmark -c config_my_system.yaml
```

> **Önemli:** `clear_memory()` metodunun tüm verileri tamamen sildiğinden emin olun. Bu metot başarısız olursa benchmark hemen durur — çapraz veri kirliliği hiçbir koşulda tolere edilmez.

---

## Hızlı Referans — Sık Kullanılan Komutlar

```bash
# Varsayılan benchmark (MESA, 200 senaryo, 5 iterasyon)
python -m mesa_benchmark -c config.yaml

# Reproducibility raporu (5 seed)
python scripts/reproduce_benchmark.py --seeds 42,43,44,45,46

# MESA vs Zep karşılaştırması (p-value ile)
python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml \
  --baseline-config mesa-benchmark/config_zep.yaml

# LoCoMo uluslararası benchmark
python scripts/download_locomo.py
python -m mesa_benchmark -c config_locomo.yaml

# HuggingFace'e yayımla
HF_TOKEN=xxx python scripts/publish_to_hf.py --repo-id your-org/mesa-benchmark

# Docker ile çalıştır
docker build -t mesa-benchmark .
docker run --env-file .env mesa-benchmark

# Temiz başlangıç (state temizle)
rm state.json results_*.jsonl
```
