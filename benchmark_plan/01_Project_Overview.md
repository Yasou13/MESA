## 1. Executive Summary (Yönetici Özeti)

MESA Benchmark Suite v4, yapay zeka destekli bellek (AI Memory) ve hibrit bilgi getirme (Hybrid Retrieval) sistemlerini standart, bağımsız ve bilimsel bir temelde değerlendirmek için tasarlanmış kapsamlı bir test ve analiz altyapısıdır.

MESA Benchmark Suite, MESA (Memory, Epistemic, and Salience Architecture) projesinin basit bir eklentisi veya alt modülü **değildir**. Aksine, pazar lideri ve akademik düzeydeki çeşitli bellek sistemlerinin (MESA, Mem0, Zep, Letta, Cognee, BareRAG vb.) birbirleriyle adil, izole ve tamamen tekrar üretilebilir (reproducible) koşullarda karşılaştırılmasını sağlayan **bağımsız bir araştırma ve mühendislik framework'üdür.**

## 2. The Problem Statement (Problem Tanımı)

Günümüzdeki RAG (Retrieval-Augmented Generation) benchmark'ları (örn. RAGAS, ARES, TruLens), genellikle **durumsuz (stateless)** sistemleri test etmek üzerine kuruludur. Statik bir belge seti üzerinden tek seferlik sorguların doğruluk oranını ölçerler.

Ancak yeni nesil AI Agent'lar ve uzun bağlamlı sistemler, **durumlu (stateful)** ve zaman içinde evrilen bellek yapılarına ihtiyaç duyar. Mevcut literatürde aşağıdaki yetenekleri uçtan uca, standart bir metrik setiyle değerlendirebilen bir framework bulunmamaktadır:

1. **Epistemic Contradiction Resolution (Çelişki Çözümü):** Belleğe zamanla eklenen ve birbiriyle çelişen bilgilerin (örneğin kullanıcının taşınması, fikir değiştirmesi veya sistem güncellemeleri) nasıl çözümlendiği.
    
2. **Temporal State Management (Zamansal Durum Yönetimi):** Bilginin zamansal geçerliliğinin anlaşılması ve eski bilginin yeni bilgi ışığında "geçersiz" (obsolete) kılınabilmesi.
    
3. **Multi-Hop Graph Reasoning:** Parçalı bilgilerin, farklı oturumlardan (session) toplanarak sentezlenmesi.
    
4. **Preference & Identity Tracking:** Kullanıcıya ait kişisel tercihlerin ve örtük kimlik detaylarının sürekli güncellenen bir profilde tutulması.
    

MESA Benchmark Suite, bu sistemleri izole ederek "bellek evrimini" uçtan uca ölçülebilir ve sayısallaştırılabilir hale getirmeyi hedefler.

## 3. Vision & Core Objectives (Vizyon ve Temel Hedefler)

Benchmark sisteminin temel hedefleri şunlardır:

- **Bağımsızlık (Vendor-Agnosticism):** Sistemin çekirdeği hiçbir bellek sağlayıcısına (MESA dahil) bağımlı olmayacaktır. Her sistem bir "Client Adapter" arayüzü aracılığıyla sisteme dahil edilecektir.
    
- **Akademik Raporlama (Paper-Ready Outputs):** Test sonuçları doğrudan bir akademik makalede veya Whitepaper'da kullanılabilecek kalitede, p-value, güven aralıkları (confidence intervals) ve istatistiksel anlamlılık (statistical significance) hesaplamalarıyla birlikte verilmelidir.
    
- **Çok Boyutlu Değerlendirme:** Sadece doğruluğa (Accuracy) odaklanmak yerine, bir bellek sisteminin üretim ortamındaki maliyetleri de (Latency, Token Efficiency, Index Growth) ölçülmelidir.
    
- **Genişletilebilirlik (Extensibility):** Yeni bir LLM, yeni bir embedding modeli, yeni bir değerlendirme metriği veya yeni bir rakip bellek sistemi, çekirdek mimariyi bozmadan projeye entegre edilebilmelidir.
    

## 4. Architectural Philosophy (Mimari Felsefe)

Bu dokümantasyon setini okuyacak her otonom ajan veya geliştirici, aşağıdaki tasarım ilkelerine uymak zorundadır:

### 4.1. The "Apple-to-Apple" Rule (Adil Karşılaştırma Kuralı)

Hiçbir sistemin, prompt mühendisliği hileleriyle haksız avantaj elde etmesine izin verilmez. Benchmark koşulunda çalıştırılan her sistem:

- Aynı temel LLM'i (örn. GPT-4o, Claude-3.5-Sonnet) kullanmak zorundadır.
    
- Aynı Embedding modelini kullanmak zorundadır.
    
- Veri yükleme (ingestion) aşamasında aynı parça boyutlarını (chunk size/overlap) kullanmalıdır.
    
- Aynı Donanım/Platform konfigürasyonu üzerinden değerlendirilmelidir.
    

### 4.2. Separation of Concerns (Sorumlulukların Ayrılması)

- **Dataset Engine:** Sadece veriyi sağlamakla yükümlüdür. Modelleri bilmez.
    
- **Client Engine:** Sadece benchmark motorunun taleplerini hedef sistemin anlayacağı API çağrılarına çevirir. Değerlendirme yapmaz.
    
- **Evaluator Engine:** Gelen cevapları (ground truth ile) karşılaştırır. API gecikmelerini veya token sayımlarını önemsemez.
    
- **Metrics Engine:** Evaluator'dan çıkan ham boolean/skor verilerini alır, istatistiksel hesaplamaları (MRR, Hit@K) yapar.
    

## 5. Scope Definition (Kapsam Tanımı)

### In-Scope (Kapsam Dahilinde Olanlar)

- Hedeflenen bellek sistemleri: MESA, Mem0, Zep, Letta, Cognee, BaseRAG, BM25.
    
- Test edilecek katmanlar: Contradiction, Multi-hop, Temporal, Entity Linking, Preference.
    
- Çıktılar: JSON bazlı test sonuçları, Markdown raporlar, PDF grafikler.
    
- Analizler: Accuracy (Doğruluk) analizleri ve Production (Performans/Maliyet) analizleri.
    
- Ablation Testleri: Bir mimarinin alt bileşenlerinin kapatılarak katkısının ölçülmesi (örn. MESA - Graph OFF).
    

### Out-of-Scope (Kapsam Dışı Olanlar)

- Benchmark sistemi kendi başına LLM fine-tuning işlemlerini yapmaz.
    
- Üçüncü parti sistemlerin (örn. Zep sunucusu) kurulumu benchmark motorunun sorumluluğunda değildir (bunların testten önce ayağa kaldırılmış olması beklenir).
    
- Görsel veya İşitsel bellek (Multimodal Memory) v4 aşamasında kapsam dışıdır; sistem tamamen metin (Text) tabanlıdır.
    

## 6. Definitions & Terminology (Terminoloji)

Dokümantasyonun geri kalanında sıklıkla kullanılacak kavramlar:

- **Target System / Client:** Test edilen spesifik bellek veya RAG yapısı.
    
- **Run / Execution:** Bir dataset üzerindeki tüm query'lerin hedef sisteme gönderilip sonuçların alındığı tekil işlem döngüsü.
    
- **Ground Truth:** Dataset içinde belirtilen, hedeflenen kesin doğru yanıt veya geri getirilmesi beklenen (expected context) spesifik metin parçası.
    
- **Ingestion Phase:** Sisteme bilgilerin yüklendiği ve indekslendiği pasif aşama.
    
- **Query Phase:** Sisteme soruların sorulduğu ve bellekten bilginin çağrıldığı aktif aşama.
    
- **Noise Ratio (Gürültü Oranı):** Doğru cevabı bulmayı zorlaştırmak için veri setine eklenen, konuyla alakasız (Red Herring) veya çeldirici bilgilerin toplam veriye oranı.
    

## 7. Success Criteria (Başarı Kriterleri)

Benchmark Suite v4'ün başarılı kabul edilebilmesi için şu koşulları sağlaması şarttır:

1. Bir geliştirici, maksimum 50 satırlık yeni bir `client_adapter.py` yazarak yeni piyasaya çıkmış bir bellek kütüphanesini sisteme dahil edebilmelidir.
    
2. Tüm pipeline tek bir CLI komutuyla (örn. `python -m mesa_benchmark run --suite full --client mesa_v1`) başlatılabilmeli ve sonuçlar klasörüne otomatik rapor bırakabilmelidir.
    
3. Çalıştırma esnasında herhangi bir hedefin (client) hata vermesi durumunda (örn. API Rate Limit), framework çökmeyecek, kaldığı yeri `state.json` üzerinde tutacak ve yeniden başlatıldığında kaldığı yerden devam edebilecektir (Resilience).
    

**[Next Step]:** _Bu dokümanda sistemin vizyonu ve genel mimari felsefesi tanımlanmıştır. Bir sonraki belge olan **02_System_Architecture.md** dosyasında, yukarıda felsefesi anlatılan modüllerin birbirleriyle olan fiziksel bağlantıları, veritabanı kararları, kuyruk yapıları (varsa) ve temel nesne diyagramları (Class & Data Flow) detaylandırılacaktır._