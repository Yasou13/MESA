## 1. Roadmap Philosophy (Yol Haritası Felsefesi)

MESA Benchmark Suite, karmaşık ve çok katmanlı bir projedir. Otonom kodlama asistanlarının (Claude, GPT, vb.) halüsinasyon görmeden projeyi inşa edebilmesi için geliştirme süreci **"Aşağıdan Yukarıya" (Bottom-Up)** ve kesin bir Sprint (Koşu) mantığıyla tasarlanmıştır.

Bir sprint tamamlanıp test edilmeden (Unit Test), kesinlikle bir sonraki sprint'e geçilmeyecektir.

## 2. Sprint Planning (Sprint Planlaması)

### Sprint 1: Core Engine & Orchestration (Sistem Omurgası)

**Hedef:** Sistemin kalbi olan Runner'ın ve Pydantic modellerinin ayağa kaldırılması. Henüz LLM veya Client bağlantısı yoktur.

- **Task 1.1:** `core/config.py` yazılarak `config.yaml` dosyasını okuyan ve doğrulayan Pydantic modelleri oluşturulacak.
    
- **Task 1.2:** `core/exceptions.py` oluşturulup özel hata sınıfları (`BenchmarkError`, `ClientTimeoutError`) tanımlanacak.
    
- **Task 1.3:** `core/state_manager.py` kodlanacak. Kapanıp açılmalarda `state.json` okunarak kalındığı yerden devam etme (Resilience) mantığı kurulacak.
    
- **Task 1.4:** `core/runner.py` iskeleti (Mock verilerle dönebilen bir event loop) yazılacak.
    
- **Deliverable:** CLI üzerinden `python -m mesa_benchmark` çalıştırıldığında konfigürasyonu okuyup hatasız kapanan boş bir döngü.
    

### Sprint 2: Data Provider Layer (Veri Yönetimi)

**Hedef:** JSON veri setlerinin okunup sistemin anlayacağı `BenchmarkScenario` nesnelerine dönüştürülmesi.

- **Task 2.1:** `datasets/schemas.py` dosyasına `MemoryContext`, `BenchmarkQuestion` modelleri eklenecek.
    
- **Task 2.2:** `datasets/loader.py` yazılarak JSON validasyon kuralları (ID eşsizliği, Context bütünlüğü) kodlanacak.
    
- **Task 2.3:** "Contradiction" ve "Multi-hop" klasörlerine 5'er soruluk **Dummy (Sahte)** JSON veri setleri konulacak.
    
- **Deliverable:** Runner çalıştırıldığında dataset'i okuyup, her bir senaryoyu ve içindeki soruları ekrana (stdout) basabilen bir yapı.
    

### Sprint 3: Client Adapters (Adaptörler ve İzolasyon)

**Hedef:** Sistemlerin framework'e bağlanabilmesi için standart ABC (Abstract Base Class) yapısının kurulması.

- **Task 3.1:** `clients/base_client.py` yazılarak `initialize`, `clear_memory`, `add_memory`, `answer` arayüzleri kilitlenecek.
    
- **Task 3.2:** `clients/dummy_client.py` oluşturulacak. Bu sadece gelen sorulara rastgele yanıt ve `latency` dönen bir test adaptörü olacak.
    
- **Task 3.3:** Runner, Dataset'ten aldığı veriyi Dummy Client'a paslayacak ve `BenchmarkResponse` alacak şekilde bağlanacak.
    
- **Deliverable:** Verinin sistemden geçip Dummy adaptörden yanıt olarak döndüğü, loglanabilir tam bir akış (Full Pipeline Draft).
    

### Sprint 4: Evaluation Engine (Değerlendirme)

**Hedef:** Yanıtların matematiksel/mantıksal olarak doğrulanması.

- **Task 4.1:** `evaluators/base_evaluator.py` interface'i yazılacak.
    
- **Task 4.2:** `evaluators/exact_match.py` yazılarak alt-dizgi (substring) ve birebir eşleşme algoritmaları eklenecek.
    
- **Task 4.3:** Evaluator modülü Runner'a entegre edilecek. Dönen yanıt, Ground Truth ile kıyaslanıp sonuçlar (0 veya 1) diske (JSONL) yazılacak.
    
- **Deliverable:** Soru -> Cevap -> Puanlama döngüsünün kusursuz çalıştığı, diske ham sonuçların kaydedildiği bir sürüm.
    

### Sprint 5: Metrics & Reporting (Raporlama Katmanı)

**Hedef:** Diskteki ham sonuçların istatistiklere dönüştürülmesi.

- **Task 5.1:** `metrics/calculator.py` kodlanarak `Hit@K`, `MRR` formülleri implement edilecek.
    
- **Task 5.2:** `reports/reporter.py` kodlanacak. Markdown formatında tablo üreten ve P95 Latency hesaplayan fonksiyonlar eklenecek.
    
- **Deliverable:** Benchmark bitiminde `reports/` klasörüne Leaderboard tablosunu ve özet istatistikleri çıkaran sistem.
    

### Sprint 6: MESA & Mem0 Integration (Gerçek Sistemler)

**Hedef:** Sahte sistemler yerine gerçek AI Memory sistemlerinin entegrasyonu.

- **Task 6.1:** `clients/mesa_client.py` kodlanacak. MESA'nın Graph ve Vector DB çağrıları bu adaptöre sarılacak.
    
- **Task 6.2:** `clients/mem0_client.py` kodlanarak referans (Baseline) rakip sisteme eklenecek.
    
- **Deliverable:** Tam teşekküllü, gerçek sistemleri yarıştıran Benchmark Suite v1.0.