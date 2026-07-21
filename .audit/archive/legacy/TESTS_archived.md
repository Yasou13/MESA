# Tests

| Finding ID | Test | Command | Expected Result | Actual Result | Status | Evidence |
|---|---|---|---|---|---|---|
| — | — | N/A | — | — | FAILED | - |
| — | 70 seçili test | N/A | Fonksiyonel test: sentetik env/mock provider ile seçili davranışlar | 70 geçti; isolation: Failed / Not verified due to SEC-001; 4,94 sn | PASSED | - |
| — | pytest --collect-only | N/A | Testlerin importsuz/servissiz toplanması | 70 toplandı; 1,10 sn | FAILED | - |
| — | pytest-cov seçili alt küme | N/A | Hedef modüllerin satır kapsamı | Hedef modüller %95; production release coverage değildir | PASSED | - |
| — | OllamaAdapter testleri | N/A | Yerel Ollama ile adapter davranışı | Çalıştırılmadı | FAILED | - |
| — | Live adapter testleri | N/A | Sağlayıcı bağlantısı | Çalıştırılmadı | FAILED | - |
| — | Mem0 testi | N/A | Harici adapter/storage | Çalıştırılmadı | FAILED | - |
| — | gather tabanlı testler | N/A | 50+ concurrent query | Çalıştırılmadı | FAILED | - |
| — | run_benchmark | N/A | Async IO ölçümü | Çalıştırılmadı | FAILED | - |
| — | Locust senaryosu | N/A | HTTP yük testi | Çalıştırılmadı | FAILED | - |
| — | 5 test dosyası | N/A | Dataset/client/evaluator pipeline | Çalıştırılmadı | FAILED | - |
| — | CLI harness | N/A | Uzun süreli kalite ölçümü | Çalıştırılmadı | FAILED | - |
| CI uygunluğu | Dosya/komut | N/A | Bağımlılık | CI uygunluğu | FAILED | - |
| Normal pytest regression gerekli | `.audit/runtime/faz9/` source assertion + `python -m py_compile mesa_memory/consolidation/loop.py` | N/A | Uygulama importu yok | Normal pytest regression gerekli | FAILED | - |
