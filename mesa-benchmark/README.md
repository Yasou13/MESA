# MESA Benchmark Suite v4

MESA Benchmark Suite, yapay zeka bellek (Memory/RAG) sistemlerini objektif, adil ve yalıtılmış bir ortamda ("Apple-to-Apple") test etmek için geliştirilmiş bağımsız bir ölçümleme altyapısıdır. MESA bellek mimarisinin yanı sıra Mem0, Zep, Letta/MemGPT gibi dış sistemleri de destekler.

## Özellikler

- **200+ Senaryo, 4 Zorluk Katmanı:** Single-Hop (%40), Multi-Hop (%30), Hard-Negative (%15), Out-of-Domain (%15)
- **5 Bellek Sistemi:** MESA, Mem0, Zep, Letta/MemGPT ve BareRAG (kontrol grubu)
- **Üç Kademeli Değerlendirme:** ExactMatch → LLM-Judge → Multi-Model Judge (majority voting)
- **Metodolojik Doğrulama:** Keyword ↔ LLM-Judge agreement rate + Cohen's Kappa
- **Root-Cause Attribution:** Hataların kaynağını otomatik tespit (RETRIEVAL_MISS, CONTEXT_NOISE, LLM_REASONING_ERROR)
- **İstatistiksel Güvenilirlik:** Multi-seed çalıştırma, Mean ± Std, Welch's t-test p-value
- **Tam İzolasyon:** Her iterasyon öncesi bellek sıfırlama, exponential backoff
- **Kesintiden Devam:** `.state.json` ile kaldığı iterasyondan otomatik devam
- **Reproducibility:** Docker + pinned dependencies (`requirements-lock.txt`)

## Desteklenen Sistemler

| Sistem | Adapter | Config | Kurulum |
|--------|---------|--------|---------|
| **MESA** | `MesaClientAdapter` | `config.yaml` | Yerleşik |
| **Mem0** | `Mem0ClientAdapter` | `config_mem0.yaml` | `pip install mem0ai` |
| **Zep** | `ZepClientAdapter` | `config_zep.yaml` | `pip install zep-cloud` |
| **Letta/MemGPT** | `LettaClientAdapter` | `config_letta.yaml` | `pip install letta` |
| **BareRAG** | `DummyClientAdapter` | — | Yerleşik |

## Hazır Config Dosyaları

| Config | Sistem | Veri Seti | Açıklama |
|--------|--------|-----------|----------|
| `config.yaml` | MESA | comprehensive_200 | Ana benchmark (200 senaryo) |
| `config_beam.yaml` | MESA | beam | BEAM karşılaştırma (20 senaryo, 400 soru) |
| `config_contradiction.yaml` | MESA | contradiction_200 | Çelişki çözümü odaklı (200 senaryo) |
| `config_multi_hop.yaml` | MESA | comprehensive_multihop | Yalnızca multi-hop (60 senaryo) |
| `config_reranking.yaml` | MESA | comprehensive_200 | CrossEncoder reranking etkin |
| `config_mem0.yaml` | Mem0 | comprehensive_200 | Mem0 baseline |
| `config_zep.yaml` | Zep | comprehensive_200 | Zep baseline |
| `config_letta.yaml` | Letta | comprehensive_200 | Letta/MemGPT baseline |
| `config_mini_mesa.yaml` | MESA | mini (2 senaryo) | Hızlı doğrulama testi |
| `config_mini_mem0.yaml` | Mem0 | mini (2 senaryo) | Hızlı doğrulama testi |

## Hızlı Başlangıç

Detaylı konfigürasyon, adaptör yazımı ve daha fazlası için **[USAGE_GUIDE.md](./USAGE_GUIDE.md)** dosyasına göz atın.

### 1. Kurulum

```bash
cd mesa-benchmark
pip install -r requirements-lock.txt
```

### 2. Yapılandırma

```bash
cp .env.example .env
# .env dosyasında API anahtarlarınızı düzenleyin
```

### 3. Çalıştırma (Proje kök dizininden)

```bash
# Hızlı doğrulama testi (mini dataset)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_mini_mesa.yaml --seeds 42

# Ana benchmark (200 senaryo)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42

# Multi-seed reproducibility (5 seed)
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config.yaml --seeds 42,43,44,45,46

# Mem0 baseline karşılaştırma
venv/bin/python scripts/reproduce_benchmark.py \
  --config mesa-benchmark/config_mem0.yaml --seeds 42
```

### 4. Docker ile Çalıştırma

```bash
docker build -t mesa-benchmark .
docker run --env-file .env mesa-benchmark
```

## Testler

```bash
pip install -r requirements-dev.txt
venv/bin/python -m pytest mesa-benchmark/tests/ -v
```

## Metodoloji

Benchmark metodolojisinin detayları için **[BENCHMARK_METHODOLOGY.md](../BENCHMARK_METHODOLOGY.md)** dosyasına bakın.

## Lisans

Apache License 2.0 — MESA Araştırma Ekibi tarafından geliştirilmiştir.
