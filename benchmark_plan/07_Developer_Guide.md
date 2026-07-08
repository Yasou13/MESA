## 1. Introduction (Giriş)

Bu rehber, MESA Benchmark Suite üzerinde çalışacak otonom ajanlar, açık kaynak katılımcıları veya kurum içi geliştiriciler için standart operasyon prosedürlerini (SOP) içerir.

Tüm kod değişiklikleri, mimari kuralları ihlal etmeden yapılmalıdır. `BenchmarkRunner` sınıfının iç mantığına doğrudan müdahale etmek kesinlikle yasaktır; tüm genişletmeler (Extensions) arayüzler (Interfaces) üzerinden yapılmalıdır.

## 2. How to Add a New Memory Client? (Yeni Bir Sistem Nasıl Eklenir?)

Rakip veya yeni nesil bir RAG/Memory sistemini (Örn. Zep) sisteme eklemek için yalnızca `clients/` klasöründe çalışmanız yeterlidir.

**Adım 1:** `clients/` klasöründe yeni bir dosya oluşturun: `zep_client.py`. **Adım 2:** `AbstractBenchmarkClient` sınıfını miras alın (inherit). **Adım 3:** Sözleşmeyi (Contract) eksiksiz uygulayın:

Python

```
from typing import Dict, Any
from clients.base_client import AbstractBenchmarkClient, BenchmarkResponse
from datasets.schemas import MemoryContext

class ZepClient(AbstractBenchmarkClient):
    
    def initialize(self, config: Dict[str, Any]) -> None:
        # Zep SDK'sını başlat
        from zep_python import ZepClient as ZepSDK
        self.sdk = ZepSDK(base_url=config.get("url"))
        self.session_id = config.get("session_id", "benchmark_test")

    def clear_memory(self) -> None:
        # Zep'in o session'a ait hafızasını sıfırla
        self.sdk.memory.delete_memory(self.session_id)

    def add_memory(self, context: MemoryContext) -> float:
        import time
        start = time.perf_counter()
        # Bağlamı sisteme ekle
        self.sdk.memory.add_memory(self.session_id, [{"content": context.text}])
        return (time.perf_counter() - start) * 1000  # ms cinsinden

    def answer(self, query: str) -> BenchmarkResponse:
        import time
        start = time.perf_counter()
        
        # Sistemi sorgula
        results = self.sdk.memory.search_memory(self.session_id, query)
        
        # Yanıtı standart BenchmarkResponse nesnesine çevir
        return BenchmarkResponse(
            answer=results[0].content if results else "No answer",
            retrieved_contexts=[r.metadata.get("id") for r in results],
            latency_ms=(time.perf_counter() - start) * 1000,
            token_usage={"prompt": 0, "completion": 0} # Desteklenmiyorsa 0 dön
        )
```

**Adım 4:** Yeni istemciyi (client) `core/config.py` içindeki desteklenen istemciler listesine (Registry) kaydedin.

## 3. How to Add a New Benchmark Dataset? (Yeni Veri Seti Nasıl Eklenir?)

Kod yazmanıza gerek yoktur, sadece JSON manipülasyonu yeterlidir.

1. `datasets/` dizini altında yeni bir klasör oluşturun (Örn: `datasets/entity_linking/v1/`).
    
2. Bir `metadata.json` dosyası oluşturun ve `tier`, `description` bilgilerini girin.
    
3. Kapsamlı bir `dataset.json` oluşturun. JSON Şemasının `04_Dataset_Specification.md` standartlarına tam uyduğundan emin olun.
    
4. Framework, bir sonraki çalışmada bu klasörü otomatik olarak tarayacak ve kullanılabilir senaryolar arasına ekleyecektir.
    

## 4. How to Write a Custom Evaluator? (Özel Değerlendirici Nasıl Yazılır?)

Varsayılan `ExactMatch` veya `LLMJudge` size yetmiyorsa, özel bir doğrulama kuralı yazabilirsiniz (Örn. Regex kullanarak IBAN doğrulayan bir Evaluator).

**Adım 1:** `evaluators/` klasöründe `regex_evaluator.py` oluşturun. **Adım 2:** `BaseEvaluator` sınıfını miras alın:

Python

```
import re
from evaluators.base_evaluator import BaseEvaluator, EvaluationResult

class RegexEvaluator(BaseEvaluator):
    
    def evaluate(self, ground_truth: str, system_output: str, **kwargs) -> EvaluationResult:
        pattern = re.compile(ground_truth) # Ground Truth'u Regex pattern'i kabul et
        match = pattern.search(system_output)
        
        return EvaluationResult(
            is_correct=bool(match),
            score=1.0 if match else 0.0,
            reasoning=f"Regex '{ground_truth}' eşleşmesi {'başarılı' if match else 'başarısız'}.",
            metrics={}
        )
```

**Adım 3:** JSON veri setindeki soru bloğunda `"evaluation_strategy": "regex"` değerini kullanın. Runner, otomatik olarak bu sınıfı tetikleyecektir.

## 5. Development Environment & Testing (Çalışma Ortamı ve Test)

Projede değişiklik yaparken, sistem bütünlüğünü bozmamak için kodlamayı şu düzende test edin:

- **Local Debugging:** Kodu `Tier 2 - Developer Mode` seviyesinde test edin. Devasa LLM çağrıları yapmak yerine, 2-3 soruluk Dummy JSON'lar kullanarak modüller arası veri iletimini (Data transfer) denetleyin.
    
- **Unit Tests:** `tests/` klasörü altında `pytest` kullanarak her modülü (özellikle `MetricsEngine` formüllerini) izole olarak test edin. `MRR` fonksiyonunun boş liste (`[]`) geldiğinde hata fırlatmak yerine `0.0` döndüğünden emin olun.
    
- **Typing Checks:** Her PR (Pull Request) öncesi `mypy .` çalıştırılarak Tip İpuçlarının (Type Hints) doğruluğu denetlenmelidir.