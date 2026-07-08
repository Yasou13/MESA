Ana Markdown spesifikasyonları tamamlandıktan sonra, projenin tamamen "kendi kendini açıklayan" (self-documenting) bir yapıya kavuşması için şu klasörlerin içleri doldurulmalıdır:

### 1. `diagrams/`

Burada Mermaid.js veya Draw.io formatında sistemin görsel haritaları tutulur.

- `architecture.mermaid`: Bölüm 2'deki yüksek seviye mimarinin görsel kodlaması.
    
- `data_flow.mermaid`: Sorunun veri setinden çıkıp LLM'e gitmesi ve rapora dönüşmesinin sequence (sıra) diyagramı.
    

### 2. `schemas/`

Sistemin strict (katı) veri tiplerini dış sistemlere açtığı yerdir.

- `dataset_schema_v1.json`: Yeni bir veri seti oluşturmak isteyenlerin VS Code'da doğrulama (validation) yapabilmesi için saf JSON Schema tanımı.
    

### 3. `templates/`

Yapay zeka asistanlarının (veya insanların) sıfırdan dosya yaratırken kopyalayacağı iskelet dosyalardır.

- **`new_client_template.py`:** İçinde `AbstractBenchmarkClient` implementasyonu bulunan, metotların içi `pass` ile geçilmiş, docstringleri hazır olan Python dosyası. Modelden yeni bir client yazması istendiğinde bu şablonu baz alması emredilir.
    

### 4. `examples/`

Değerlendirme (Evaluation) örneklerini içerir.

- `example_report.md`: Sistemin üreteceği mükemmel bir raporun taslağı (Leaderboard tabloları, Hit@K grafikleri yer tutucuları ile). Bu sayede `reports/reporter.py` kodlanırken çıktının neye benzemesi gerektiği LLM tarafından bilinir.