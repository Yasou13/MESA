## 1. Dataset Architecture & Philosophy (Veri Seti Mimarisi ve Felsefesi)

MESA Benchmark Suite, veri setlerini kod tabanından tamamen izole eder. Sistemdeki hiçbir test (Contradiction, Multi-hop, Temporal vb.) koda gömülü (hardcoded) değildir. Tüm veri setleri `.json` dosyalarında tutulur ve sıkı bir Pydantic şeması (Schema) ile doğrulanır.

**Temel Kurallar:**

1. **İmmutability (Değişmezlik):** Veri seti JSON dosyaları, yayınlandıktan (v1, v2) sonra asla değiştirilemez. Yeni bir test senaryosu eklenecekse, o veri setinin yeni bir versiyonu (`v2`) oluşturulur.
    
2. **Kendini Açıklama (Self-Contained):** Bir test senaryosu (Scenario), sistemin o testi çözmek için ihtiyaç duyduğu **tüm** bağlamı (Context), soruyu (Query) ve cevabı (Ground Truth) içinde barındırmalıdır. Dış kaynaklara (İnternet araması, Wikipedia API'si vb.) bağımlı olunamaz.
    

## 2. Directory Structure (Veri Seti Klasör Yapısı)

Veri setleri mantıksal kategorilere ve versiyonlara göre ayrılır. Her klasörde bir `metadata.json` ve asıl veriyi içeren `dataset.json` bulunmalıdır.

Plaintext

```
datasets/
│
├── contradiction/
│   └── v1/
│       ├── metadata.json
│       └── dataset.json
│
├── multi_hop/
│   └── v1/
│       ├── metadata.json
│       └── dataset.json
│
└── temporal/
    └── v2/
        ├── metadata.json
        └── dataset.json
```

## 3. The Universal JSON Schema (Evrensel JSON Şeması)

Tüm benchmark veri setleri aşağıdaki ana JSON formatına uymak zorundadır. Bu yapı, `03_Engineering_Specification.md` içindeki Pydantic modellerine birebir eşlenir.

**Dosya Örneği: `datasets/contradiction/v1/dataset.json`**

JSON

```
{
  "benchmark_type": "contradiction_resolution",
  "version": "1.0",
  "scenarios": [
    {
      "scenario_id": "CR-001",
      "description": "Kullanıcının taşınması sebebiyle güncellenen lokasyon bilgisi.",
      "contexts": [
        {
          "id": "ctx_1",
          "text": "Merhaba, ben 2020 yılından beri İstanbul'da yaşıyorum.",
          "metadata": {
            "timestamp": "2023-01-15T10:00:00Z",
            "source": "user_chat"
          }
        },
        {
          "id": "ctx_2",
          "text": "Artık İstanbul'un kalabalığından sıkıldım, geçen ay İzmir'e taşındım ve buraya yerleştim.",
          "metadata": {
            "timestamp": "2024-05-20T14:30:00Z",
            "source": "user_chat"
          }
        }
      ],
      "questions": [
        {
          "id": "q_1",
          "query": "Şu an hangi şehirde yaşıyorum?",
          "ground_truth": "İzmir",
          "expected_contexts": ["ctx_2"],
          "evaluation_strategy": "exact_match"
        }
      ]
    }
  ]
}
```

## 4. Scenario Topologies (Senaryo Topolojileri)

Sistemin test edeceği yeteneklere göre veri setlerinin karakteristik özellikleri şunlardır:

### 4.1. Contradiction Resolution (Tier 1)

Bu veri setlerinde `contexts` listesinde kasıtlı olarak birbiriyle çelişen bilgiler verilir.

- **Amaç:** Bellek sisteminin sadece "İstanbul" ve "İzmir" kelimelerini bulması değil, zaman damgası (timestamp) veya anlamsal yapı üzerinden **eski bilginin geçersiz kılındığını** (invalidation) anlamasıdır.
    

### 4.2. Multi-Hop Reasoning (Tier 2)

Bu veri setlerinde cevaba tek bir bağlam ile ulaşılamaz.

- `ctx_1`: "Şirketin CEO'su Ahmet Yılmaz'dır."
    
- `ctx_2`: "Ahmet Yılmaz'ın en sevdiği renk mavidir."
    
- **Soru:** "Şirket CEO'sunun en sevdiği renk nedir?"
    
- **Beklenen:** Bellek sistemi Graph veya RAG mekanizmalarıyla `ctx_1` ve `ctx_2`'yi birleştirerek (hop) cevabı üretmelidir. `expected_contexts` içinde her iki ID de yer almak zorundadır.
    

### 4.3. Noise Injection & Red Herrings (Tier 10)

Sistemin dayanıklılığını (Robustness) ölçmek için, bağlam listesinin içine konuyla tamamen alakasız veya kelime olarak çok benzeyen ama anlamsal olarak yanlış çeldirici bağlamlar (Red Herrings) eklenir.

- **Metric:** Sistem gürültü oranı (Noise Ratio) %10'dan %80'e çıktığında Retrieval Accuracy düşüş eğrisi incelenir.
    

## 5. Dataset Validation Rules (Veri Seti Doğrulama Kuralları)

`DatasetLoader` (veri okuyucu modül), test başlamadan önce aşağıdaki kuralları (Validation) çalıştırır. Herhangi bir kural ihlal edilirse, sistem `DatasetValidationError` fırlatır ve benchmark başlatılmaz.

1. **ID Uniqueness:** Bir veri setindeki tüm `scenario_id`, `ctx_id` ve `q_id` değerleri evrensel olarak benzersiz (unique) olmalıdır.
    
2. **Context Integrity:** `questions` bloğundaki `expected_contexts` listesinde yer alan id'ler, `contexts` listesinde mutlaka tanımlı olmalıdır. Olmayan bir ID'nin gelmesi beklenemez.
    
3. **Evaluation Strategy Match:** Soru bloğunda belirtilen `evaluation_strategy` (örn. `exact_match`, `llm_judge`), framework'ün desteklediği evaluator'lar listesinde (Registry) kayıtlı olmalıdır.
    

## 6. Metadata Definition (Meta Veri Tanımı)

Her veri seti klasöründe bulunması zorunlu olan `metadata.json`, sistemin raporlama aşamasında başlıkları ve açıklamaları doğru oluşturmasını sağlar.

JSON

```
{
  "name": "MESA Multi-Hop Benchmark",
  "tier": 2,
  "description": "Tests the system's ability to chain multiple disparate facts to answer a single query.",
  "total_scenarios": 500,
  "average_contexts_per_scenario": 5.4,
  "author": "MESA Research Team",
  "tags": ["graph-reasoning", "multi-hop", "rag"]
}
```