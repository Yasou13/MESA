# MESA Benchmark Methodology

Bu doküman, MESA Benchmark Suite v4'ün metodolojik ilkelerini, değerlendirme pipeline'ını ve bilimsel geçerlilik garantilerini tanımlar.

---

## 1. Tasarım İlkeleri

### 1.1 Apple-to-Apple Karşılaştırma
Tüm bellek sistemleri (MESA, Mem0, Zep, Letta/MemGPT) aynı `AbstractBenchmarkClient` arayüzünü uygulamak zorundadır. Bu, aynı veri seti, aynı evaluator ve aynı metriklerle değerlendirilmelerini garanti eder.

### 1.2 Top-K Enforcement
Tüm sistemlere eşit retrieval limiti uygulanır: **Top-K = 5** (`top_n=5`). Hiçbir sistem daha fazla bağlam çekerek yapay avantaj elde edemez.

### 1.3 Embedding Model Paritesi
MESA, yerel ortamda `sentence-transformers/all-MiniLM-L6-v2` kullanır. Baseline sistemler kendi embedding modellerini kullanabilir, ancak bu fark raporda belirtilir.

### 1.4 Tam İzolasyon
Her iterasyon öncesi `clear_memory()` çağrılır. Bu çağrı başarısız olursa benchmark **derhal** durur (`MemoryPurgeError`). Çapraz veri kirliliği hiçbir koşulda tolere edilmez.

---

## 2. Veri Seti Mimarisi

### 2.1 Comprehensive Dataset (200 Senaryo)
Dört zorluk katmanı bulunur:

| Katman | Oran | Senaryo | Test Edilen Yetenek |
|--------|------|---------|---------------------|
| **Single-Hop** | %40 | 80 | Tek bellek düğümünden doğrudan bilgi getirme |
| **Multi-Hop** | %30 | 60 | 2+ bellek düğümü arasında çizge geçişi |
| **Hard-Negative** | %15 | 30 | Eski bilgi vs. güncel bilgi çelişki çözümü |
| **Out-of-Domain** | %15 | 30 | İlgisiz bilgiyi karantinaya alma |

### 2.2 Özel Veri Setleri

| Veri Seti | Dosya | Senaryolar | Açıklama |
|-----------|-------|------------|----------|
| `comprehensive_200_dataset.json` | `mesa_benchmark/datasets/` | 200 | Ana benchmark veri seti |
| `mini_dataset.json` | `mesa_benchmark/datasets/` | 2 | Hızlı doğrulama testi |
| `stress_dataset.json` | `mesa_benchmark/datasets/` | 100 | Stres testi |
| `beam/dataset.json` | `datasets/` | 20 (400 soru) | BEAM karşılaştırma seti |
| `contradiction_200.json` | `datasets/` | 200 | Çelişki çözümü odaklı |
| `comprehensive_multihop_only.json` | `datasets/` | 60 | Yalnızca multi-hop senaryolar |

### 2.3 Senaryo Formatı
Her senaryo, `contexts` (sisteme yüklenecek bilgiler) ve `questions` (sorulacak sorular + beklenen cevaplar) içerir. Sorular `expected_context_ids` ile hangi bağlamların getirilmesi gerektiğini belirtir.

---

## 3. Değerlendirme Pipeline'ı (Üç Kademeli)

### 3.1 Kademe 1: Exact Match (Ücretsiz)
Alt dize (substring) eşleşmesi ile hızlı ve deterministik değerlendirme.

### 3.2 Kademe 2: LLM-as-a-Judge (Tek Model)
Karmaşık multi-hop ve çelişki senaryolarında basit string matching yetersiz kalır. Bu durumda bir LLM model (ör. `qwen3:8b`) anlamsal değerlendirme yapar:
- Prompt, ground truth ve sistem çıktısını içerir
- Model `{is_correct, score, reasoning}` JSON formatında yanıt verir
- **Ensemble voting** (varsayılan 3 çağrı) ile güvenilirlik artırılır

### 3.3 Kademe 3: Multi-Model Judge (Bağımsız Değerlendirme)
Self-grading bias'ı engellemek için 2-3 farklı LLM model kullanılır:
- Her model aynı prompt'u bağımsız olarak değerlendirir
- **Majority voting** ile final karar verilir
- Modeller arası **pairwise agreement** oranı hesaplanır

### 3.4 Agreement Rate (Metodolojik Doğrulama)
Keyword evaluator ile LLM-Judge arasındaki uyum otomatik hesaplanır:
- **Agreement Rate (%)**: İki evaluator'ın aynı karara vardığı oran
- **Cohen's Kappa**: Şans uyumunu çıkaran istatistiksel uyum katsayısı (-1.0 → 1.0)
- **Contingency Table**: Detaylı çapraz tablo

---

## 4. Metrikler

### 4.1 Retrieval Metrikleri

| Metrik | Formül | Açıklama |
|--------|--------|----------|
| **Hit@K** (K=1,3,5) | 1 if any expected ID ∈ top-K | Doğru bağlamın ilk K sonuçta bulunma oranı |
| **MRR** | 1/rank of first relevant | Ortalama İlk Bulma Sırası |
| **nDCG@5** | DCG/iDCG | Normalize Edilmiş İndirgenmiş Kümülatif Kazanç |

### 4.2 Doğruluk Metrikleri

| Metrik | Açıklama |
|--------|----------|
| **Accuracy** | Doğru cevap sayısı / Toplam soru sayısı |
| **Avg Score** | LLM Judge'ın verdiği ortalama skor (0.0-1.0) |

### 4.3 Performans Metrikleri

| Metrik | Açıklama |
|--------|----------|
| **Avg Latency** | Ortalama sorgu yanıt süresi (ms) |
| **P95 Latency** | Sorguların %95'inin tamamlanma süresi |
| **P99 Latency** | En yavaş %1'lik sorguların süresi |
| **Token Efficiency** | Doğru cevap başına harcanan token sayısı |

### 4.4 Diagnostik Metrikler (Root-Cause Attribution)
Her başarısız sorgu otomatik olarak kategorize edilir:

| Kategori | Anlam |
|----------|-------|
| `RETRIEVAL_MISS` | Beklenen bağlam Vektör/Çizge tarafından bulunamadı |
| `CONTEXT_NOISE` | Doğru bağlam geldi ama aşırı gürültü LLM'i şaşırttı |
| `LLM_REASONING_ERROR` | Doğru bağlam geldi ama LLM cevabı çıkaramadı |
| `TIMEOUT_OR_ERROR` | Sorgu zaman aşımına uğradı veya exception fırlatıldı |

---

## 5. Reproducibility (Tekrarlanabilirlik)

### 5.1 Multi-Seed Çalıştırma
LLM'ler stokastik olduğundan, güvenilir sonuçlar için en az 5 farklı seed ile çalıştırma önerilir. `reproduce_benchmark.py` scripti:
- Her seed için bağımsız çalıştırma yapar
- Mean ± Std hesaplar
- Welch's t-test ile istatistiksel anlamlılık test eder

### 5.2 Determinizm
- `seed` parametresi ile Python `random` ve `numpy.random` seed'lenir
- Docker + `requirements-lock.txt` ile ortam sabitlenir
- `.state.json` ile kesintiden devam mekanizması

### 5.3 Resilience
- **Exponential Backoff**: API hataları ve rate limit'lerde katlanarak artan bekleme (1s, 2s, 4s)
- **Timeout Koruması**: `concurrent.futures.ThreadPoolExecutor` ile takılma önlenir
- **Noise Parity**: Kaldığı yerden devam ederken önceki bağlamlar geri yüklenir

---

## 6. Rapor Formatı
Her benchmark çalıştırması şu çıktıları üretir:

1. **`results_{run_id}.jsonl`**: Her soru için detaylı JSON kayıtları (skor, latency, diagnostik)
2. **`report_{run_id}.md`**: İnsan tarafından okunabilir Markdown rapor
3. **`.state.json`**: Kesintiden devam durumu
4. **`reproducibility_report.json`**: Multi-seed istatistikleri

---

## 7. Bilinen Kısıtlamalar

1. **Yerel Ollama Modeli**: Zero-cost modda `qwen3:8b` kullanılır. Thinking token'ları nedeniyle her LLM Judge değerlendirmesi 5-15 saniye sürebilir.
2. **Self-Judge Bias**: Aynı model hem MESA'nın retrieval pipeline'ında hem LLM Judge olarak kullanıldığında, `multi_judge_models` ile bağımsız değerlendirme önerilir.
3. **Hit@K Sınırlaması**: `expected_context_ids` belirtilmemiş sorularda Hit@K metriği hesaplanamaz; yalnızca LLM Judge skoru kullanılır.
