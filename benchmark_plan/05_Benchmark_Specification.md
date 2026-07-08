## 1. Benchmark Scope & Philosophy (Kapsam ve Felsefe)

MESA Benchmark Suite, bellek sistemlerini sadece ikili (doğru/yanlış) bir eksende değerlendirmez. Bir yapay zeka belleğinin üretim ortamında (production) kullanılabilirliğini kanıtlamak için Retrieval (Bilgi Getirme), Reasoning (Akıl Yürütme) ve Efficiency (Verimlilik) boyutlarında testler uygular.

Tüm metrikler ve görevler (Tiers), tekrarlanabilirlik ilkesi gereği matematiksel olarak kesin tanımlara bağlanmıştır.

## 2. Benchmark Layers (Benchmark Katmanları)

Sistem iki ana test katmanında çalışır:

### 2.1. Layer 1: Standard Benchmarks

Literatürdeki mevcut sistemlerle (RAG, Long-Context LLMs) adil bir kıyaslama yapmak için tasarlanmıştır. Bu katman dışarıdan entegre edilen veri setlerini çalıştırır:

- **Needle in a Haystack (NIAH):** 1M+ token bağlamı içine gizlenmiş tek bir bilginin (Needle) getirilme başarısı.
    
- **LongMemEval:** Uzun bağlamlı sohbet geçmişlerinde bilgi tutma testi.
    

### 2.2. Layer 2: MESA Core Benchmarks

Geleneksel RAG sistemlerinin (BareRAG, BM25) başarısız olduğu, ancak MESA gibi "Stateful" ve "Graph-based" sistemlerin parlaması beklenen spesifik yetenek testleridir.

- **Tier 1 - Contradiction (Çelişki):** Eski bilgiyle yeni bilgi çatıştığında sistemin en güncel ve geçerli olanı seçme yeteneği.
    
- **Tier 2 - Multi-Hop:** En az 2, en fazla 5 farklı kaynaktan parça parça bilgi toplayıp sentezleme yeteneği.
    
- **Tier 4 - Temporal Memory:** Olayların kronolojik sırasını kavrama ("Geçen yaz neredeydim?" sorusuna zaman damgalarını kullanarak doğru yanıt verme).
    
- **Tier 6 - Entity Linking:** Farklı dokümanlarda geçen "O", "Şirket", "Bizim CEO" gibi zamir ve dolaylı anlatımları doğru düğümlere (Node/Entity) bağlayabilme.
    

## 3. Mathematical Evaluation Metrics (Matematiksel Değerlendirme Metrikleri)

Bir modelin "bağlam getirme" (Retrieval) başarısı, `metrics/calculator.py` tarafından aşağıdaki matematiksel formüllerle hesaplanır.

### 3.1. Hit@K

İstenilen (Ground Truth) bağlam parçasının, sistemin döndürdüğü ilk $K$ sonuç içinde yer alma durumudur. İkili (Binary) bir metriktir.

- Sistem 5 bağlam döndürdü. İstenilen bilgi 3. sıradaysa **Hit@3 = 1**, **Hit@1 = 0** olur.
    

### 3.2. Mean Reciprocal Rank (MRR)

Sistemin doğru cevabı ne kadar "yukarıda" getirdiğini ölçer. İlk sıradaki doğru cevap en yüksek puanı alır. Formülü şu şekildedir:

$$MRR = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{rank_i}$$

Burada $|Q|$ toplam sorgu sayısını, $rank_i$ ise doğru cevabın döndürülen listedeki sırasını ifade eder (Eğer doğru cevap listede yoksa, $\frac{1}{rank_i} = 0$ kabul edilir).

### 3.3. Normalized Discounted Cumulative Gain (nDCG)

Özellikle Multi-Hop gibi birden fazla doğru bağlamın (Context) getirilmesi gereken senaryolarda kullanılır. Getirilen bilgilerin alaka düzeyini ve sıralamasını ölçer.

$$DCG_p = \sum_{i=1}^{p} \frac{rel_i}{\log_2(i+1)}$$

$$nDCG_p = \frac{DCG_p}{IDCG_p}$$

_(IDCG, mümkün olan en ideal sıralamanın DCG skorudur.)_

## 4. Performance & Scalability Metrics (Performans ve Ölçeklenebilirlik)

Bir sistem çok doğru sonuç üretebilir, ancak yanıt süresi 15 saniyeyse üretim ortamı (Production) için işlevsizdir. Değerlendirme motoru şu metrikleri zorunlu olarak loglar:

- **P95 / P99 Latency:** Sorguların %95'inin (veya %99'unun) milisaniye cinsinden tamamlanma süresi. Uç değerlerin (outliers) sistem kararlılığını bozup bozmadığını ölçer.
    
- **Token Efficiency:** MESA gibi Graph sistemlerinin, bağlam penceresine (Context Window) BareRAG'a kıyasla daha az ama daha öz bilgi verip vermediğini ölçer:
    
    $$Efficiency\_Ratio = \frac{Total\_Prompt\_Tokens}{Accurate\_Answers\_Count}$$
    
- **Memory Growth Rate:** Sisteme 100, 1.000 ve 10.000 bağlam eklendiğinde Vector DB ve Graph DB'nin diskte/RAM'de kapladığı alanın büyüme eğrisi (Logaritmik vs. Lineer).
    

## 5. Statistical Significance (İstatistiksel Anlamlılık)

MESA Benchmark, akademik kalite standardını korumak için sonuçların tesadüfi olmadığını kanıtlamak zorundadır.

Tüm Tier'lar (Görevler) en az **5 kez (Iterations)** farklı random seed'ler ile çalıştırılır. Rapor jeneratörü (`reports/`) sadece ortalamayı vermekle kalmaz, rakip sistemler arasında **Welch's t-test** uygular.

- **Null Hypothesis ($H_0$):** MESA ile Mem0'ın ortalama doğruluğu arasında fark yoktur.
    
- Eğer $p-value < 0.05$ çıkarsa, istatistiksel olarak MESA'nın (veya diğer sistemin) anlamlı bir üstünlüğü olduğu raporlanır ve liderlik tablosuna (Leaderboard) onay rozeti (✓) ile işlenir.
    

## 6. Ablation Studies (Bileşen Testleri)

MESA mimarisinin gücünün nereden geldiğini kanıtlamak için, sistem kendi içinde alt konfigürasyonlarla da test edilir. `config.yaml` üzerinden şu konfigürasyonlar (Ablations) çalıştırılır ve kıyaslanır:

1. **Full MESA:** Tüm bileşenler aktif.
    
2. **MESA - Graph OFF:** Sadece Vektör tabanlı bilgi getirme çalışır. (Bilgi sentezinin Graph'tan gelip gelmediğini ispatlar).
    
3. **MESA - Consensus OFF:** Epistemik çelişki çözücü (Epistemic Validator) kapatılır. (Çelişki çözme başarısının bu modülden geldiğini ispatlar).