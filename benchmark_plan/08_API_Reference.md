## 1. Core Execution API (`core/runner.py`)

`BenchmarkRunner`, tüm bileşenleri orkestre eden en üst seviyedeki yürütme sınıfıdır.

### 1.1. Sınıf Tanımı: `BenchmarkRunner`

Python

```
class BenchmarkRunner:
    def __init__(
        self,
        client: AbstractBenchmarkClient,
        loader: DatasetLoader,
        evaluator: BaseEvaluator,
        config: BenchmarkConfig
    ) -> None: ...
```

- **`client`**: `AbstractBenchmarkClient` arayüzünü uygulayan ve test edilecek hedef bellek istemcisi nesnesi.
    
- **`loader`**: İlgili benchmark veri setini diskten okuyan ve doğrulayan `DatasetLoader` nesnesi.
    
- **`evaluator`**: Sistem çıktılarını Ground Truth ile kıyaslayan `BaseEvaluator` nesnesi.
    
- **`config`**: Yürütme parametrelerini barındıran `BenchmarkConfig` Pydantic modeli.
    

### 1.2. Metot: `run_suite`

Tüm suite senaryolarını sırayla tetikleyen ana döngü metodudur.

Python

```
def run_suite(self) -> List[Dict[str, Any]]:
    """
    Seçilen veri setindeki tüm senaryoları sırayla çalıştırır.
    
    Returns:
        List[Dict[str, Any]]: Her bir sorgunun ham sonuçlarını, harcanan zamanı,
                              token kullanımını ve doğruluk skorunu içeren sözlük listesi.
    Raises:
        MemoryPurgeError: Herhangi bir senaryo öncesi bellek temizliği başarısız olursa.
    """
```

### 1.3. Metot: `resume_from_checkpoint`

Çöken bir benchmark çalışmasını `state.json` üzerinden ayağa kaldıran kurtarma metodudur.

Python

```
def resume_from_checkpoint(self, checkpoint_path: str) -> None:
    """
    Belirtilen durum dosyasını okur, DatasetLoader'ın imlecini günceller
     ve benchmark'ı kaldığı yerden devam ettirir.
    
    Args:
        checkpoint_path (str): state.json dosyasının fiziksel yolu.
    """
```

## 2. Dataset Provider API (`datasets/loader.py`)

Veri setlerinin doğrulanmasından ve belleğe akıtılmasından (streaming/batching) sorumlu sınıftır.

### 2.1. Sınıf Tanımı: `DatasetLoader`

Python

```
class DatasetLoader:
    def __init__(self, dataset_name: str, version: str) -> None: ...
```

### 2.2. Metot: `load_scenarios`

Python

```
def load_scenarios(self) -> Generator[BenchmarkScenario, None, None]:
    """
    İlgili dataset.json dosyasını Pydantic şemasıyla doğrular ve 
    senaryoları sırayla bir Generator (Yield) olarak döndürür.
    
    Yields:
        BenchmarkScenario: Sıradaki test senaryosu nesnesi.
    Raises:
        DatasetValidationError: JSON şeması geçersiz veya ID eşsizliği bozuksa.
    """
```

## 3. Evaluator API (`evaluators/`)

Sistem yanıtlarının kalitesini puanlayan alt sistemlerin fonksiyon sözleşmeleridir.

### 3.1. Sınıf Tanımı: `LLMJudgeEvaluator`

Python

```
class LLMJudgeEvaluator(BaseEvaluator):
    def __init__(self, judge_model: str, temperature: float = 0.0) -> None: ...
```

### 3.2. Metot: `evaluate`

Python

```
def evaluate(
    self,
    ground_truth: str,
    system_output: str,
    expected_contexts: List[str],
    retrieved_contexts: List[str]
) -> EvaluationResult:
    """
    Hakem LLM'e (LLM-as-a-Judge) özel tasarlanmış bir prompt göndererek
    sistemin ürettiği yanıtı ve getirdiği bağlamları analiz eder.
    
    Args:
        ground_truth (str): Gerçek doğru yanıt.
        system_output (str): Test edilen sistemin ürettiği yanıt.
        expected_contexts (List[str]): Getirilmesi beklenen gerçek kaynak ID'leri.
        retrieved_contexts (List[str]): Sistem tarafından getirilen kaynak ID'leri.
        
    Returns:
        EvaluationResult: Doğruluk durumu (bool), skor (0.0 - 1.0) ve gerekçe metni.
    """
```