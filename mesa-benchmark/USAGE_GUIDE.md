# MESA Benchmark Suite - Kullanım Kılavuzu

Bu kılavuz, benchmark altyapısının konfigürasyonu, veri setlerinin nasıl ekleneceği, yeni adaptörlerin (client) nasıl yazılacağı ve değerlendirme modüllerinin detaylarını içerir.

---

## 1. Konfigürasyon Yapısı (`config.yaml`)

Benchmark yürütme motoru `config.yaml` dosyasından beslenir. Bu dosyayı değiştirerek test edeceğiniz sistemi, çalıştırılacak iterasyon sayısını ve istatistikleri belirleyebilirsiniz.

```yaml
suite_name: "Sprint 2 Integration Suite"
iterations: 5   # İstatistiksel tutarlılık için testin kaç kez tekrarlanacağı
seed: 42

dataset:
  name: "contradiction_dummy"
  version: "v1"
  path: "datasets/contradiction/v1/dataset.json" # Çalıştırılacak veri seti

client:
  name: "dummy_client"  # Örnek: mesa_client veya mem0_client
  adapter_class: "mesa_benchmark.clients.dummy_client.DummyClientAdapter"
  timeout_ms: 10000
  parameters: {} # Client'a özel parametreler (ör. MESA DB Path)

evaluation:
  metrics:
    - "hit_at_k"
    - "mrr"
    - "latency"
  llm_judge_model: "gpt-4o" # LLM Judge evaluator kullanılacaksa model adı
```

---

## 2. Veri Seti Mimarisi

Sistem, soruları ve bağlamları JSON dosyaları üzerinden okur. Kendi veri setinizi oluşturmak isterseniz `datasets/` dizini altında yeni bir klasör yapısı oluşturmanız gerekir.

**Klasör Yapısı Örneği:**
```text
datasets/
└── temporal_memory/
    └── v1/
        ├── metadata.json
        └── dataset.json
```

**dataset.json Örneği:**
```json
{
  "scenario_id": "TEMP-01",
  "name": "Zaman Serisi Testi",
  "contexts": [
    {
      "id": "ctx_1",
      "text": "Bugün hava güneşli.",
      "metadata": {"timestamp": "2023-01-01"}
    }
  ],
  "questions": [
    {
      "id": "q_1",
      "query": "Hava nasıl?",
      "ground_truth": "güneşli",
      "expected_context_ids": ["ctx_1"],
      "evaluation_strategy": "exact_match"
    }
  ]
}
```

### Değerlendirme Stratejileri (`evaluation_strategy`)
- `"exact_match"`: Hedef sistemin döndürdüğü yanıt (answer_text), alt dize (substring) olarak `ground_truth` içeriyorsa doğru kabul edilir. Basit, hızlı ve ücretsizdir.
- `"llm_judge"`: Soru, hedef sistemin cevabı ve ground_truth bir LLM'e (Örn. GPT-4o) gönderilir. Yargıç LLM, cevabın doğruluğunu anlamsal olarak analiz eder ve puanlar. (Not: Çalışması için `OPENAI_API_KEY` gereklidir).

---

## 3. Yeni Bellek İstemcisi (Client Adapter) Eklemek

Rakip bir AI belleğini (Örneğin: Zep, Mem0) sisteme entegre edip MESA ile yarışmasını isterseniz tek yapmanız gereken `AbstractBenchmarkClient` sınıfını uygulamaktır.

1. `mesa_benchmark/clients/` altına yeni bir dosya açın (örn: `zep_client.py`).
2. Sınıfınızı tanımlayın:

```python
from mesa_benchmark.clients.base import AbstractBenchmarkClient, BenchmarkResponse
from mesa_benchmark.datasets.schemas import MemoryContext, BenchmarkQuestion
import time

class ZepClientAdapter(AbstractBenchmarkClient):
    
    def initialize(self, config_params: dict) -> None:
        # Zep API bağlantısı vb.
        pass

    def clear_memory(self) -> None:
        # İzolasyon için sistemi SIFIRLAYIN (Önemli!)
        pass

    def add_memory(self, context: MemoryContext) -> dict:
        start = time.perf_counter()
        # Bağlamı bellek veritabanına ekle
        return {"latency_ms": (time.perf_counter() - start) * 1000}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start = time.perf_counter()
        # Sistemden cevap al
        return BenchmarkResponse(
            answer_text="Bulunan cevap",
            retrieved_context_ids=["ctx_1"], # Sistemin bulduğu belgelerin ID'leri
            latency_ms=(time.perf_counter() - start) * 1000,
            token_usage={"prompt": 50, "completion": 20}
        )
```

3. `config.yaml` dosyanızda `adapter_class` yolunu bu yeni sınıfınıza yönlendirin.

---

## 4. İzolasyon ve Kesintiden Devam Etme (Resilience)

Benchmark aracı son derece katı bir hata kontrol sistemine sahiptir.

- **Exponential Backoff:** Herhangi bir API Limit aşımında (Rate Limit) araç çökmez, katlanarak artan bekleme süreleriyle (1s, 2s, 4s...) tekrar dener.
- **Kaldığı Yerden Devam:** Sistem çalışırken bilgisayarınız kapanırsa panik yapmayın. Tüm ilerleme `state.json` dosyasında tutulur. Sistemi tekrar çalıştırdığınızda (`python -m mesa_benchmark`) araç otomatik olarak `state.json` dosyasını bulur ve testleri baştan sarmak yerine son kalınan sorudan okumaya devam eder.

Eğer bilinçli olarak temiz bir test başlatmak istiyorsanız, çalıştırmadan önce `state.json` dosyasını silmelisiniz.

---

## 5. Raporların Okunması

Test tamamlandıktan sonra `reports/` dizini altında testinizin saatini ve benzersiz kimliğini (Run ID) taşıyan bir Markdown (`.md`) dosyası oluşur.

Bu dosyada:
- **Accuracy:** Sistem yanıtlarının toplam doğruluk oranı.
- **Hit@K:** Gerçekte kullanılması gereken bilgilerin, sistemin belleğinden getirdiği ilk 1, 3 veya 5 bilginin içinde bulunma oranıdır (Belleğin bilgi getirme kalitesi).
- **MRR (Mean Reciprocal Rank):** Doğru bilginin arama sonuçlarında ne kadar üst sıralarda yer aldığının ortalama ölçüsü.
- **P99 Gecikme:** Sistem gecikmelerinin en kötü %1'lik kısmını (en yavaş yanıtları) gösterir. Stres altında sistemin yavaşlama kapasitesini belirtir.
