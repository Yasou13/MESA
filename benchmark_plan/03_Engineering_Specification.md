## 1. Engineering Scope (Mühendislik Kapsamı)

Bu doküman, MESA Benchmark Suite sisteminin kod tabanındaki (codebase) kesin dosya yapısını, sınıf hiyerarşilerini (class hierarchies), veri transfer nesnelerini (DTOs/Data Transfer Objects) ve modüller arası API sözleşmelerini tanımlar.

Proje, sıkı bir şekilde **Type Hinting (Tip Belirleme)** kullanan, Pydantic ile veri doğrulayan ve Abstract Base Class (ABC) yapılarıyla polimorfizm sağlayan modern bir Python (>= 3.10) mimarisiyle geliştirilecektir.

## 2. Detailed Directory Structure (Detaylı Dizin Yapısı)

Projeyi geliştirecek model/geliştirici, aşağıdaki dosya hiyerarşisine harfiyen uymalıdır. Bu yapı, `02_System_Architecture.md` dosyasında belirtilen katmanların fiziksel karşılığıdır.

Plaintext

```
mesa_benchmark/
│
├── core/
│   ├── __init__.py
│   ├── runner.py            # İçerik: BenchmarkRunner sınıfı
│   ├── config.py            # İçerik: BenchmarkConfig Pydantic modeli
│   ├── state_manager.py     # İçerik: StateManager (Resume/Checkpoint mantığı)
│   └── exceptions.py        # İçerik: BenchmarkError, ClientTimeoutError
│
├── datasets/
│   ├── __init__.py
│   ├── loader.py            # İçerik: DatasetLoader sınıfı
│   └── schemas.py           # İçerik: DatasetScenario, MemoryContext Pydantic modelleri
│
├── clients/
│   ├── __init__.py
│   ├── base_client.py       # İçerik: AbstractBenchmarkClient (ABC)
│   ├── mesa_client.py       # İçerik: MesaClient(AbstractBenchmarkClient)
│   └── mem0_client.py       # İçerik: Mem0Client(AbstractBenchmarkClient)
│
├── evaluators/
│   ├── __init__.py
│   ├── base_evaluator.py    # İçerik: BaseEvaluator (ABC)
│   ├── exact_match.py       # İçerik: ExactMatchEvaluator(BaseEvaluator)
│   └── llm_judge.py         # İçerik: LLMJudgeEvaluator(BaseEvaluator)
│
└── metrics/
    ├── __init__.py
    ├── calculator.py        # İçerik: MetricsEngine (MRR, Hit@K hesaplamaları)
    └── reporter.py          # İçerik: ReportGenerator
```

## 3. Data Models & Schemas (Veri Modelleri - DTOs)

Veri akışı sırasında dictionary (`{}`) kullanılmayacaktır. Katmanlar arası iletişim, `pydantic.BaseModel` veya Python `dataclasses` üzerinden yapılacaktır.

Dosya: `datasets/schemas.py`

Python

```
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class MemoryContext(BaseModel):
    """Sisteme yüklenecek olan tekil bilgi parçası."""
    id: str
    text: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class BenchmarkQuestion(BaseModel):
    """Sisteme sorulacak soru ve beklenen cevap."""
    id: str
    query: str
    ground_truth: str
    expected_contexts: Optional[List[str]] = Field(default_factory=list) # Geri gelmesi beklenen doküman ID'leri

class BenchmarkScenario(BaseModel):
    """DatasetLoader'dan çıkan ve Runner'a verilen standart görev paketi."""
    scenario_id: str
    contexts: List[MemoryContext]
    questions: List[BenchmarkQuestion]
```

## 4. Client Adapter Contract (Adaptör Arayüzü Sözleşmesi)

Bir yapay zeka bellek sisteminin benchmark'a dahil olabilmesi için `AbstractBenchmarkClient` sınıfından türetilmesi zorunludur.

Dosya: `clients/base_client.py`

Python

```
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from datasets.schemas import MemoryContext

class BenchmarkResponse(BaseModel):
    """Adaptörden dönecek standart yanıt formatı."""
    answer: str
    retrieved_contexts: List[str]  # Sadece ID'ler veya kaynak metinler
    latency_ms: float
    token_usage: Dict[str, int]    # {"prompt": 150, "completion": 50}

class AbstractBenchmarkClient(ABC):
    
    @abstractmethod
    def initialize(self, config: Dict[str, Any]) -> None:
        """Veritabanı bağlantılarını ve LLM tanımlarını yapar."""
        pass

    @abstractmethod
    def clear_memory(self) -> None:
        """Her senaryodan önce sistemi sıfırlar (Purge/Drop). Katı kuraldır."""
        pass

    @abstractmethod
    def add_memory(self, context: MemoryContext) -> float:
        """
        Bilgiyi sisteme ekler.
        Dönüş değeri: İşlem süresi (latency_ms)
        """
        pass

    @abstractmethod
    def answer(self, query: str) -> BenchmarkResponse:
        """
        Soruyu alır, bellekten bağlamı getirir ve yanıt üretir.
        Gerekiyorsa RAG pipeline'ını (Retrieve + Generate) uçtan uca tetikler.
        """
        pass
```

## 5. Execution Logic (Runner Sınıfı İmzası)

`BenchmarkRunner`, senaryoları (scenario) çalıştıran ana döngüdür. Sistem hatalarını yakalamalı ve `state.json` dosyasına yazmalıdır.

Dosya: `core/runner.py`

Python

```
from clients.base_client import AbstractBenchmarkClient
from datasets.loader import DatasetLoader
from evaluators.base_evaluator import BaseEvaluator
from core.state_manager import StateManager

class BenchmarkRunner:
    def __init__(
        self, 
        client: AbstractBenchmarkClient, 
        loader: DatasetLoader, 
        evaluator: BaseEvaluator
    ):
        self.client = client
        self.loader = loader
        self.evaluator = evaluator
        self.state = StateManager()

    def run_suite(self) -> None:
        """
        Ana döngü: 
        1. Loader'dan sıradaki Scenario'yu al.
        2. Client memory'sini temizle.
        3. Context'leri yükle (add_memory).
        4. Questions'ları sor (answer).
        5. Sonuçları Evaluator'a yolla.
        6. State'i kaydet.
        """
        pass
```

## 6. Evaluator Contract (Değerlendirici Sözleşmesi)

Değerlendirme algoritmalarının ortak arayüzü.

Dosya: `evaluators/base_evaluator.py`

Python

```
from abc import ABC, abstractmethod
from typing import Dict, Any

class EvaluationResult(BaseModel):
    is_correct: bool
    score: float             # 0.0 ile 1.0 arası
    reasoning: str           # Özellikle LLM-as-a-judge için açıklama
    metrics: Dict[str, Any]  # Gecikme, token maliyeti vb. ekstra metrikler

class BaseEvaluator(ABC):
    
    @abstractmethod
    def evaluate(
        self, 
        ground_truth: str, 
        system_output: str, 
        expected_contexts: List[str], 
        retrieved_contexts: List[str]
    ) -> EvaluationResult:
        """
        Yanıtın ve getirilen bağlamın doğruluğunu ölçer.
        """
        pass
```

## 7. Exception Handling (Hata Yönetimi)

Benchmark saatlerce sürebilir, bu yüzden hiçbir API hatası (örn. OpenAI Rate Limit) ana `Runner` döngüsünü **çökertmemelidir**.

- `ClientTimeoutError`: Hedef sistem 30 saniyeden uzun süre yanıt vermezse fırlatılır. `Runner` bu hatayı yakalar, `is_correct=False` ve `score=0` yazar, loglar ve bir sonraki soruya geçer.
    
- `MemoryPurgeError`: `clear_memory()` başarısız olursa fırlatılır. Bu kritik bir hatadır; benchmark izolasyonu bozulduğu için framework çalışmayı anında durdurur (Hard Fail).