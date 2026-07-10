# MESA Benchmark Suite v4

MESA Benchmark Suite, yapay zeka bellek (Memory/RAG) sistemlerini objektif, adil ve yalıtılmış bir ortamda ("Apple-to-Apple") test etmek için geliştirilmiş bağımsız bir ölçümleme altyapısıdır. MESA bellek mimarisinin yanı sıra Mem0, Zep, Letta/MemGPT gibi dış sistemleri de destekler.

## Özellikler

- **200+ Senaryo, 4 Zorluk Katmanı:** Single-Hop (%40), Multi-Hop (%30), Hard-Negative (%15), Out-of-Domain (%15)
- **5 Bellek Sistemi:** MESA, Mem0, Zep, Letta/MemGPT ve BareRAG (kontrol grubu)
- **Üç Kademeli Değerlendirme:** ExactMatch → LLM-Judge → Multi-Model Judge (GPT + Claude majority voting)
- **Metodolojik Doğrulama:** Keyword ↔ LLM-Judge agreement rate + Cohen's Kappa
- **İstatistiksel Güvenilirlik:** 5-seed çalıştırma, Mean ± Std, Welch's t-test p-value
- **Uluslararası Benchmark:** LoCoMo (ECAI 2025) entegrasyonu
- **Tam İzolasyon:** Her iterasyon öncesi bellek sıfırlama, exponential backoff
- **Kesintiden Devam:** `state.json` ile kaldığı iterasyondan otomatik devam
- **Reproducibility:** Docker + pinned dependencies (`requirements-lock.txt`)
- **HuggingFace Yayın:** Tek komutla dataset + card yükleme

## Desteklenen Sistemler

| Sistem | Adapter | Config | Kurulum |
|--------|---------|--------|---------|
| **MESA** | `MesaClientAdapter` | `config.yaml` | Yerleşik |
| **Mem0** | `Mem0ClientAdapter` | — | `pip install mem0ai` |
| **Zep** | `ZepClientAdapter` | `config_zep.yaml` | `pip install zep-cloud` |
| **Letta/MemGPT** | `LettaClientAdapter` | `config_letta.yaml` | `pip install letta` |
| **BareRAG** | `DummyClientAdapter` | — | Yerleşik |

## Hızlı Başlangıç

Detaylı konfigürasyon, adaptör yazımı, LoCoMo entegrasyonu ve HuggingFace yayın bilgileri için **[USAGE_GUIDE.md](./USAGE_GUIDE.md)** dosyasına göz atın.

### 1. Kurulum

```bash
cd mesa-benchmark
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Pinned bağımlılıkları yükleyin (reproducibility için)
pip install -r requirements-lock.txt
```

### 2. Yapılandırma

```bash
cp .env.example .env
# .env dosyasında API anahtarlarınızı düzenleyin (OPENAI_API_KEY, ZEP_API_KEY vb.)
```

### 3. Çalıştırma

```bash
# Varsayılan benchmark (MESA, 200 senaryo, 5 iterasyon)
python -m mesa_benchmark -c config.yaml

# Reproducibility raporu (5 seed ile)
python ../scripts/reproduce_benchmark.py --seeds 42,43,44,45,46

# LoCoMo uluslararası benchmark
python scripts/download_locomo.py
python -m mesa_benchmark -c config_locomo.yaml

# Rakip karşılaştırma
python -m mesa_benchmark -c config_zep.yaml
python -m mesa_benchmark -c config_letta.yaml
```

### 4. Docker ile Çalıştırma

```bash
docker build -t mesa-benchmark .
docker run --env-file .env mesa-benchmark
```

## Testler

```bash
pip install -r requirements-dev.txt
cd .. && ./venv/bin/python -m pytest tests/test_tech_debt_fixes.py tests/test_mesa_benchmark_enhancements.py -v
```

## Lisans

Apache License 2.0 — MESA Araştırma Ekibi tarafından geliştirilmiştir.
