Aşağıda, planladığımız teknik spesifikasyonun ikinci parçası olan **02_System_Architecture.md** dokümanını sunuyorum. Bu bölüm, sistemin genel bileşenlerini, veri akışını ve modüller arası etkileşimleri tanımlayarak kodlama sürecinin omurgasını oluşturur.

# 02_System_Architecture.md

## 1. Architectural Overview (Mimari Genel Bakış)

MESA Benchmark Suite, **Pipeline (Boru Hattı)** mimarisine dayanır. Sistem, dışarıdan alınan yapılandırılmış veri setlerini (datasets) işler, soyutlanmış arayüzler (adapters) aracılığıyla hedef yapay zeka bellek sistemleriyle (Target Memory Systems) etkileşime girer ve dönen sonuçları bağımsız bir değerlendirme motoruna aktarır.

Sistem, "Sorumlulukların Ayrılması" (Separation of Concerns) prensibi gereği beş ana mantıksal katmana (Layer) bölünmüştür:

1. **Orchestration Layer (Yönetim Katmanı):** Sistemin yaşam döngüsünü, konfigürasyonları ve durum (state) yönetimini kontrol eder.
    
2. **Data Provider Layer (Veri Sağlayıcı Katman):** Veri setlerinin versiyonlanması, yüklenmesi ve standart `Task` (Görev) nesnelerine dönüştürülmesinden sorumludur.
    
3. **Execution & Adapter Layer (Çalıştırma ve Adaptör Katmanı):** Spesifik bellek sistemleriyle (MESA, Mem0, vb.) iletişimi kurar. Benchmark sisteminin dış dünya ile tek temas noktasıdır.
    
4. **Evaluation Engine (Değerlendirme Motoru):** Alınan yanıtların doğruluğunu (Accuracy) ve kalite metriklerini hesaplar.
    
5. **Analytics & Reporting Layer (Analiz ve Raporlama Katmanı):** Ham değerlendirme sonuçlarını istatistiksel verilere ve görsel çıktılara dönüştürür.
    

## 2. High-Level Component Diagram (Yüksek Seviye Bileşen Diyagramı)

Aşağıdaki şema, bir benchmark çalıştırıldığında (run) sistemdeki ana modüllerin birbirleriyle olan ilişkisini göstermektedir:

Plaintext

```
+-----------------------------------------------------------------------------------+
|                            [ Orchestration Layer ]                                |
|                               BenchmarkRunner                                     |
|  (Manages State, Iterates Dataset, Triggers Clients, Collects Results)            |
+-----------------------------------------------------------------------------------+
        |                 |                          |                      |
        v                 v                          v                      v
+---------------+  +------------------+  +----------------------+  +----------------+
| Data Provider |  | Execution Layer  |  |  Evaluation Engine   |  |   Reporting    |
|               |  |                  |  |                      |  |                |
| DatasetLoader |  | ClientAdapter    |  | Logic: Exact Match   |  | MetricsEngine  |
| SchemaParser  |  | (Base Interface) |  | Logic: LLM-as-a-Judge|  | ReportGenerator|
+---------------+  +------------------+  +----------------------+  +----------------+
        |                 |                          |                      |
        |                 v                          |                      |
        |          +------------------+              |                      |
        |          | Target Systems   |              |                      |
        |          | (MESA, Mem0, Zep)|              |                      |
        |          +------------------+              |                      |
        |                                            |                      |
        v                                            v                      v
[ datasets/ ]                              [ experiments/results ]    [ reports/ ]
(JSON Files)                               (Raw Result JSONs)         (Markdown/CSV)
```

## 3. Core Components Description (Temel Bileşen Tanımları)

### 3.1. BenchmarkRunner (`runners/`)

Sistemin beynidir. CLI'dan veya API'den gelen `config.yaml` dosyasını okur.

- Çalışma esnasında uygulamanın çökmesi durumunda devam edebilmesi için bir `state.json` (Checkpoint) tutar.
    
- Bir döngü (event loop) içerisinde `DatasetLoader`'dan veriyi çeker, `ClientAdapter`'a iletir, dönen sonucu alır ve `Evaluator`'a paslar.
    

### 3.2. DatasetManager (`datasets/`)

Farklı görevlere (Contradiction, Multi-Hop, Temporal vb.) ait ham JSON veri setlerini okur ve sistemin anlayacağı standart bir **`BenchmarkScenario`** veri sınıfına (Data Class) dönüştürür. Bu katman, veri setinin format validasyonunu (`Pydantic` vb. ile) yapmaktan sorumludur.

### 3.3. ClientAdapter (`clients/`)

Tüm yapay zeka bellek kütüphanelerinin (örn. MESA, Mem0) uymak zorunda olduğu **Abstract Base Class (ABC)** arayüzüdür.

- **Katı Kural:** BenchmarkRunner **asla** doğrudan MESA'nın veya Zep'in API'sini çağırmaz. Runner sadece `adapter.add_memory()` veya `adapter.answer()` fonksiyonlarını bilir. İçerideki kütüphane bağımlılıklarını izole eder.
    

### 3.4. Evaluator (`evaluators/`)

Hedef sistemden dönen yanıtı (Target Response) ve veri setindeki gerçek doğruyu (Ground Truth) alarak bir skor veya boolean değer üretir. Üç temel strateji destekler:

- **String Matching:** Exact Match, Substring, Regex (örn. yanıtın içinde spesifik bir ismin geçip geçmediği).
    
- **Semantic Similarity:** Cosine Similarity tabanlı vektör karşılaştırması (Embedding model aracılığıyla).
    
- **LLM-as-a-Judge:** Claude veya GPT-4 kullanılarak, "Bu yanıt, şu gerçek doğruyu kapsıyor mu?" sorusunun yanıtlanması (Özellikle karmaşık Multi-Hop veya Contradiction görevleri için).
    

### 3.5. MetricsEngine & ReportGenerator (`metrics/`, `reports/`)

Evaluator'dan gelen ham sonuç (örneğin 1000 sorudan 850'si doğru) burada alınır; Hit@K, MRR, Accuracy, nDCG gibi metrikler hesaplanır. P95 Latency ve Token Cost gibi performans metrikleriyle birleştirilip Markdown, CSV ve PDF grafiklerine dönüştürülür.

## 4. System Data Flow (Sistem Veri Akışı)

Benchmark yaşam döngüsü 4 ana aşamadan (Phase) oluşur. Aşağıdaki akış, verinin bellek sistemine girişini ve değerlendirilmesini gösterir:

### Aşama 1: Initialization (Hazırlık)

1. Kullanıcı CLI komutunu çalıştırır: `python benchmark.py --client mesa --dataset multi_hop_v1`
    
2. `BenchmarkRunner` konfigürasyonu okur.
    
3. `DatasetManager` ilgili dataset JSON dosyasını belleğe yükler.
    
4. `ClientAdapter` üzerinden hedef sistem (MESA) başlatılır (örn. veritabanı bağlantıları kurulur, bellek sıfırlanır `clear_memory()`).
    

### Aşama 2: Ingestion Phase (Veri Yükleme)

Bu aşama, RAG sistemlerinin dokümanları veya durumları belleğe yazma sürecidir. Performans (Write Latency) burada ölçülür.

1. `DatasetManager`, senaryo içindeki "Context" bloklarını sırayla alır.
    
2. `BenchmarkRunner`, bu blokları `adapter.add_memory()` metodu ile hedef sisteme iletir.
    
3. Runner, bu işlemin milisaniye (ms) cinsinden süresini ve harcanan token sayısını kaydeder.
    

### Aşama 3: Query Phase (Sorgulama)

Bu aşama, bellek sisteminin test edildiği ana kısımdır.

1. `DatasetManager`, senaryo içindeki "Question" nesnesini verir.
    
2. `BenchmarkRunner`, soruyu `adapter.answer()` (veya sadece bilgi getirme ise `adapter.retrieve()`) metoduna iletir.
    
3. Hedef sistem, kendi iç mekanizmalarını (Graph, Vector Search, LLM) kullanarak bir yanıt (Output) ve/veya bağlam listesi (Retrieved Contexts) döndürür.
    
4. Runner, geçen süreyi (Retrieval Latency) ve yanıtı kaydeder.
    

### Aşama 4: Evaluation & Reporting (Değerlendirme ve Raporlama)

1. `BenchmarkRunner`, Ground Truth ve Output nesnelerini `Evaluator`'a gönderir.
    
2. `Evaluator`, sonucun doğruluğuna karar verir (1 veya 0, ya da 0.0-1.0 arası bir skor).
    
3. Tüm veri seti tamamlandığında, `MetricsEngine` istatistiksel hesaplamaları yapar.
    
4. `ReportGenerator`, sonuçları `reports/` klasörüne yazar ve Leaderboard'u günceller.
    

## 5. Directory & Module Mapping (Dizin ve Modül Eşlemesi)

Yukarıdaki mimari, kod tarafında aşağıdaki fiziksel yapıya doğrudan karşılık gelecektir. Bu yapı, geliştiricinin (veya kod asistanının) her bir bileşeni nereye yazacağını kesinleştirir:

Bash

```
mesa_benchmark_suite/
│
├── core/                       # Sistemin omurgası (Layer 1)
│   ├── runner.py               # BenchmarkRunner class'ı
│   ├── state.py                # Checkpoint ve durum yönetimi
│   └── config.py               # YAML okuyucu ve doğrulayıcı
│
├── datasets/                   # Veri yönetimi (Layer 2)
│   ├── loader.py               # Veri okuma araçları
│   ├── schemas.py              # Pydantic modelleri (BenchmarkScenario, vb.)
│   └── files/                  # Gerçek JSON veri setleri (v1, v2)
│
├── clients/                    # Adaptörler (Layer 3)
│   ├── base.py                 # AbstractBenchmarkClient (Interface)
│   ├── mesa_client.py          # MESA implementasyonu
│   └── mem0_client.py          # Mem0 implementasyonu
│
├── evaluators/                 # Değerlendirme algoritmaları (Layer 4)
│   ├── base.py                 # BaseEvaluator class'ı
│   ├── exact_match.py          # String karşılaştırma
│   └── llm_judge.py            # LLM tabanlı doğrulayıcı
│
└── metrics/                    # Raporlama ve Analiz (Layer 5)
    ├── calculator.py           # İstatistiksel matematik operasyonları (MRR, P95)
    └── reporter.py             # Markdown, CSV, Plotly jeneratörü
```

## 6. Resilience & Fault Tolerance (Dayanıklılık ve Hata Toleransı)

Binlerce sorgu içeren bir benchmark saatler sürebilir. Ağ kopmaları veya üçüncü parti API (OpenAI/Anthropic) limitasyonları durumunda çalışmanın çöp olmaması sistem mimarisinin temel gereksinimidir.

1. **Atomic Transactions:** Her bir senaryonun yanıtı, alındığı anda anlık olarak diske (JSON Lines - `.jsonl` formatında) eklenerek (append-only) kaydedilmelidir.
    
2. **Rate Limit Handling:** `ClientAdapter` ve `Evaluator` katmanları içerisinde otomatik "Exponential Backoff" (giderek artan bekleme süresiyle yeniden deneme) mantığı bulunmalıdır.
    
3. **Resumption (Kaldığı Yerden Devam Etme):** Sistem çökerse, tekrar çalıştırıldığında `runner.py` diske kaydedilmiş son `.jsonl` satırını okumalı ve `DatasetLoader`'ın imlecini o soruya kaydırmalıdır. Bellek (Memory State) bu gibi durumlarda kirlenmemesi için, checkpoint id'leri ile senkronize edilmelidir.