## 1. Deployment Philosophy (Dağıtım Felsefesi)

MESA Benchmark Suite, ortamdan bağımsız (environment-agnostic) çalışacak şekilde tasarlanmıştır. Sistem üç farklı katmanda (Tier) çalıştırılabilir: Araştırmacılar için Colab (Research Mode), geliştiriciler için Yerel Makine (Developer Mode) ve ölçeklenebilirlik testleri için Docker/Cloud (Production Mode).

Tüm ortamlar için ortak gereksinim **Python >= 3.10** sürümüdür.

## 2. Environment Variables (Çevresel Değişkenler)

Sistemin çalışması için kök dizinde bir `.env` dosyası bulunmalıdır. Runner, `config.yaml` içindeki API key'leri doğrudan okumak yerine bu değişkenlere referans verir.

Bash

```
# .env dosyası
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-..."
ZEP_API_URL="http://localhost:8000"
MEM0_API_KEY="m0-..."
MESA_DB_PATH="./data/mesa_vectors.db"
```

## 3. Tier 2: Local Developer Setup (Yerel Geliştirici Kurulumu)

Bu mod, yeni istemciler (clients) yazmak ve kod doğrulaması (sanity check) yapmak için kullanılır.

**Adım 1:** Sanal ortam oluşturun ve bağımlılıkları yükleyin.

Bash

```
python -m venv venv
source venv/bin/activate  # Windows için: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pytest ve mypy için
```

**Adım 2:** Küçük bir veri setiyle sistemi test edin.

Bash

```
python -m core.runner --config configs/local_test.yaml --suite sanity_check
```

## 4. Tier 1: Research Mode (Google Colab Kurulumu)

Araştırmacıların bilimsel makale üretimi için GPU (T4/V100/A100) kullanarak sistemi çalıştırması gerektiğinde bu mod kullanılır.

**Adım 1:** Colab hücresinde repo klonlanır ve bağımlılıklar yüklenir.

Python

```
!git clone https://github.com/mesa-project/mesa-benchmark-suite.git
%cd mesa-benchmark-suite
!pip install -e .
```

**Adım 2:** Drive mount edilerek raporların ve `state.json` dosyasının kalıcı (persistent) olması sağlanır.

Python

```
from google.colab import drive
drive.mount('/content/drive')

!python -m core.runner --config /content/drive/MyDrive/mesa/configs/full_run.yaml \
                       --report-dir /content/drive/MyDrive/mesa/reports/
```

## 5. Tier 3: Production Mode (Docker ve Cloud)

Özellikle MESA'nın "Concurrent Users" (Eşzamanlı Kullanıcı) ve "Stress Test" gibi Production Tier senaryolarını test etmek için sistemin Dockerize edilmesi zorunludur.

**Dosya: `Dockerfile`**

Dockerfile

```
# Base Image
FROM python:3.10-slim-buster

# Çalışma dizini
WORKDIR /app

# Sistem bağımlılıkları (Derleme işlemleri için)
RUN apt-get update && apt-get install -y gcc build-essential

# Bağımlılıkları kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kaynak kodları ve veri setlerini kopyala
COPY core/ ./core/
COPY clients/ ./clients/
COPY evaluators/ ./evaluators/
COPY metrics/ ./metrics/
COPY datasets/ ./datasets/

# Çıktı klasörlerini oluştur
RUN mkdir -p /app/reports /app/logs

# Entrypoint
ENTRYPOINT ["python", "-m", "core.runner"]
CMD ["--config", "configs/default.yaml"]
```

Eğer test edilecek bellek sistemi (örneğin Zep veya Mem0) kendi veritabanlarına (PostgreSQL, Qdrant) ihtiyaç duyuyorsa, framework bir `docker-compose.yml` ile başlatılmalıdır. `docker-compose up -d` komutu, önce hedef bellek sistemini ayağa kaldırır, ardından `mesa-benchmark` konteynerini çalıştırıp testi başlatır.