## 1. Testing Philosophy (Test Felsefesi)

MESA Benchmark Suite, diğer yapay zeka sistemlerini ölçen bilimsel bir terazi görevi gördüğü için, **kendi kod tabanının hatasız ve deterministik olması** kritik bir zorunluluktur. Sistemdeki matematiksel formüllerin (MRR, Hit@K), hata yakalama bloklarının ve kurtarma mekanizmalarının doğruluğu, `pytest` kütüphanesi kullanılarak sürekli test edilmelidir.

Test stratejisi 3 ana katmandan oluşur:

1. **Unit Tests (Birim Testler):** LLM çağrılarından izole, saf matematiksel ve mantıksal fonksiyonların testi.
    
2. **Integration Tests (Entegrasyon Testleri):** Modüller arası (Runner -> Client -> Evaluator) veri akışının ve durum yönetiminin testi.
    
3. **Deterministic Mock Testing (Taklit Testleri):** LLM ve Harici DB yanıtlarının taklit edilerek (mocking) bütçeyi harcamadan pipeline doğrulama testi.
    

## 2. Unit Testing Strategy & Examples (Birim Testleri)

Özellikle `metrics/calculator.py` içindeki istatistiksel ve matematiksel hesaplamaların milimetrik doğruluğu test edilir.

### 2.1. Test Örneği: MRR (Mean Reciprocal Rank) Hesaplama Testi

Dosya: `tests/test_metrics.py`

Python

```
import pytest
from metrics.calculator import MetricsEngine

def test_mrr_calculation_perfect_score():
    engine = MetricsEngine()
    # Senaryo: Doğru cevap ilk sırada getirilmiş (Rank = 1)
    # Reciprocal Rank = 1/1 = 1.0
    ranks = [1, 1, 1]
    assert engine.calculate_mrr(ranks) == 1.0

def test_mrr_calculation_mixed_scores():
    engine = MetricsEngine()
    # Senaryo 1: İlk sırada (1/1)
    # Senaryo 2: İkinci sırada (1/2)
    # Senaryo 3: Hiç getirememiş (0)
    # Ortalama = (1.0 + 0.5 + 0.0) / 3 = 0.5
    ranks = [1, 2, 0]
    assert engine.calculate_mrr(ranks) == pytest.approx(0.5)

def test_mrr_empty_input():
    engine = MetricsEngine()
    # Boş girdi durumunda sistem çökmemeli, 0.0 dönmeli
    assert engine.calculate_mrr([]) == 0.0
```

## 3. Integration & Mock Testing (Entegrasyon ve Mock Düzeni)

Gerçek LLM API'lerine bağımlı kalmadan tüm akışı test etmek amacıyla `unittest.mock` modülü kullanılır. Böylece ağ gecikmeleri veya API maliyetleri test süreçlerini etkilemez.

### 3.1. Test Örneki: End-to-End Pipeline Mock Testi

Dosya: `tests/test_pipeline.py`

Python

```
from unittest.mock import MagicMock, patch
import pytest
from core.runner import BenchmarkRunner
from datasets.schemas import BenchmarkScenario, MemoryContext, BenchmarkQuestion

@pytest.fixture
def mock_scenario():
    """Test için sahte bir senaryo nesnesi üretir."""
    return BenchmarkScenario(
      scenario_id="TEST-01",
      contexts=[MemoryContext(id="c1", text="MESA projesi 2025'te başladı.")],
      questions=[BenchmarkQuestion(id="q1", query="MESA ne zaman başladı?", ground_truth="2025")]
    )

@patch('clients.base_client.AbstractBenchmarkClient')
@patch('evaluators.base_evaluator.BaseEvaluator')
def test_runner_event_loop_flow(MockClient, MockEvaluator, mock_scenario):
    # Mock nesnelerinin davranışlarını yapılandır
    client_instance = MockClient.return_value
    evaluator_instance = MockEvaluator.return_value
    
    # Client'ın answer metodundan dönecek sahte yanıtı ayarla
    client_instance.answer.return_value = MagicMock(
        answer="2025 yılında başladı.",
        retrieved_contexts=["c1"],
        latency_ms=120.0,
        token_usage={"prompt": 10, "completion": 5}
    )
    
    # DatasetLoader'ı taklit et ve mock_scenario döndürmesini sağla
    mock_loader = MagicMock()
    mock_loader.load_scenarios.return_value = [mock_scenario]
    
    # Config nesnesini oluştur
    mock_config = MagicMock()
    
    # Runner'ı başlat ve çalıştır
    runner = BenchmarkRunner(
        client=client_instance, 
        loader=mock_loader, 
        evaluator=evaluator_instance, 
        config=mock_config
    )
    runner.run_suite()
    
    # Sözleşmelerin doğru sırayla çağrılıp çağrılmadığını doğrula
    client_instance.clear_memory.assert_called_once()
    client_instance.add_memory.assert_called_once_with(mock_scenario.contexts[0])
    client_instance.answer.assert_called_once_with(mock_scenario.questions[0].query)
    evaluator_instance.evaluate.assert_called_once()
```

## 4. Benchmark Validation & Sanity Checks (Sağlamlık Kontrolleri)

Gerçek bir benchmark testi başlatılmadan önce sistem otomatik olarak bir **"Sanity Check" (Aklıselim Kontrolü)** tetikler.

1. **Client Connectivity Check:** İstemci, uzak sunucuya (örn. yerel ağdaki bir Zep veya Mem0 Docker konteynerine) bağlanabiliyor mu? `initialize()` çağrısı test edilir.
    
2. **Write-Read Invariance Test:** Sisteme 1 adet benzersiz rastgele anahtar kelime eklenir (`add_memory`) ve hemen ardından bu bilgi geri çağrılır (`answer`). Eğer sistem kendi eklediği ham bilgiyi sıfır gürültülü ortamda getiremiyorsa (Retrieval Failure), test motoru çalışmayı durdurur ve geliştiriciyi uyarır.