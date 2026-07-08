# MESA Benchmark Suite v4

MESA Benchmark Suite, yapay zeka bellek (Memory/RAG) sistemlerini objektif, adil ve yalıtılmış bir ortamda ("Apple-to-Apple") test etmek için geliştirilmiş bağımsız bir ölçümleme altyapısıdır. MESA bellek mimarisinin yanı sıra Mem0, Zep gibi dış sistemleri de destekleyecek şekilde tasarlanmıştır.

## Özellikler

- **Tam İzolasyon:** Her test senaryosundan önce hedef bellek sistemini otomatik olarak sıfırlayarak çapraz veri kirliliğini (data leakage) engeller.
- **Kapsamlı Metrikler:** Sadece doğruluk değil, aynı zamanda Getirme (Retrieval) yeteneklerini de ölçer: `Hit@1`, `Hit@3`, `Hit@5`, `MRR` (Mean Reciprocal Rank), `nDCG`.
- **Performans Ölçümü:** Ortalama Gecikme, `P95` ve `P99` Gecikme (Latency) hesaplamaları ile sistemin strese karşı direncini raporlar.
- **Dinamik Değerlendirme (Evaluators):** Basit yanıtlar için `ExactMatchEvaluator`, çok adımlı (Multi-Hop) karmaşık akıl yürütme senaryoları için `LLMJudgeEvaluator` (LLM-as-a-Judge) destekler.
- **Hata Toleransı (Resilience):** Uzun süren benchmark'larda API çökmeleri yaşanırsa, `state.json` mekanizması ile tam olarak kaldığı iterasyondan devam eder.
- **Otomatik Raporlama:** Her koşu (run) sonrası okunabilir, şık Markdown formatında Liderlik Tablosu (Leaderboard) raporları üretir.

## Sistem Gereksinimleri

- Python 3.10 veya üzeri
- `pip` paket yöneticisi
- Hedef sistemlere (MESA, Mem0 vb.) bağlanabilmek için gerekli API anahtarları (bkz. `.env.example`)

## Hızlı Başlangıç

Daha detaylı bilgi, veri seti yapılandırmaları ve yeni adaptör yazımı için lütfen **[USAGE_GUIDE.md](./USAGE_GUIDE.md)** dosyasına göz atın.

### 1. Kurulum

```bash
git clone https://github.com/mesa-project/mesa-benchmark-suite.git
cd mesa-benchmark
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Bağımlılıkları yükleyin
pip install -r requirements.txt
```

### 2. Yapılandırma

`.env.example` dosyasını `.env` olarak kopyalayın ve içerisindeki API anahtarlarını kendi sistemlerinize göre düzenleyin:
```bash
cp .env.example .env
```

`config.yaml` üzerinden hangi bellek istemcisini (client) ve veri setini kullanacağınızı seçin.

### 3. Çalıştırma

Benchmark'ı başlatmak için aşağıdaki komutu kullanın:

```bash
python -m mesa_benchmark --config config.yaml
```

Çıktılar `reports/` klasörüne Markdown (.md) olarak, ham loglar ise jsonl formatında ana dizine kaydedilecektir.

## Testler

Geliştirici ortamında unit test ve mock pipeline testlerini çalıştırmak için:

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests/
```

## Lisans
Bu proje MESA Araştırma Ekibi tarafından geliştirilmiştir.
