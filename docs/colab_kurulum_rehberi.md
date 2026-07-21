# MESA Google Colab Kurulum ve Kullanım Rehberi

Bu rehber, MESA (Memory Engine for Structured Agents) sistemini Google Colab üzerinde adım adım nasıl kuracağınızı ve test edeceğinizi gösterir. Google Colab ortamı, GPU desteği ve yüksek internet hızı sunduğu için ML (Makine Öğrenimi) gereksinimleri olan projeleri test etmek için idealdir.

## Adım 1: Projeyi Klonlama ve Dizine Geçiş

Öncelikle Colab not defterinizde yeni bir kod hücresi açın ve GitHub deposunu klonlayarak proje dizinine geçiş yapın:

```python
# MESA deposunu klonla
!git clone https://github.com/Yasou13/MESA.git

# Proje dizinine geçiş yap (Colab'de %cd magic komutu kullanılmalıdır)
%cd MESA
```

## Adım 2: Bağımlılıkların Yükleneceği Ortamın Hazırlanması

MESA bağımlılıklarını `pyproject.toml` üzerinden yönetir. Colab ortamında tam makine öğrenimi ve provider SDK'ları gerektiğinde ilgili optional extras kurulmalıdır.

Aşağıdaki komutu çalıştırarak gerekli Python kütüphanelerini yükleyin:

```python
# Tüm ML ve Core bağımlılıklarını kurar (REBEL model kullanımı vb. için)
!pip install -e ".[ml,adapters]"

# VEYA sadece hafif versiyonu kurmak isterseniz:
# !pip install -e .
```

> **İpucu:** Kurulum tamamlandıktan sonra Colab sizden "Runtime'ı (Çalışma Zamanını) Yeniden Başlatmanızı" isteyebilir. Eğer böyle bir uyarı çıkarsa "Restart Session" butonuna tıklayın ve ardından tekrar `%cd MESA` komutunu çalıştırarak dizinde olduğunuzdan emin olun.

## Adım 3: Çevresel Değişkenlerin (.env) Ayarlanması

MESA'nın çalışabilmesi için LLM API anahtarlarına ve sistem ayarlarına ihtiyacı vardır. Colab üzerinde `.env` dosyasını programatik olarak oluşturabilirsiniz:

```python
import os

env_icerigi = """
# LLM Sağlayıcı Ayarları (Örn: Groq, OpenAI veya Anthropic)
LLM_API_KEY=sizin_llm_api_anahtariniz_buraya
MESA_LLM_PROVIDER=openai_compatible
LLM_MODEL_NAME=llama-3.1-8b-instant
LLM_BASE_URL=https://api.groq.com/openai/v1

# MESA Sistem Ayarları
MESA_API_KEY=local-dev-key
MESA_REBEL_ENABLED=false
MESA_ZERO_COST_MODE=false
MESA_EXTRACTION_LANG=tr
"""

# .env dosyasını oluştur
with open('.env', 'w') as f:
    f.write(env_icerigi.strip())

print(".env dosyası başarıyla oluşturuldu!")
```

> **Önemli:** `LLM_API_KEY` kısmına kendi API anahtarınızı (örneğin Groq kullanıyorsanız `gsk_...` ile başlayan anahtarınızı) girmeyi unutmayın. Colab ortamında gizlilik için isterseniz Colab'in sol menüsündeki "Secrets (Anahtarlar)" bölümünü de kullanabilirsiniz.

## Adım 4: MESA Sunucusunu Başlatma ve Erken Hata Denetimi (Pre-flight Workflow)

Colab not defterleri aynı anda sadece bir hücrenin çalışmasına izin verdiği için, FastAPI sunucusunu arka planda başlatmamız gerekiyor. Sunucunun sorunsuz (500 veya 401 hatası vermeden) çalıştığından emin olmak için aşağıdaki **Erken Müdahale (Pre-flight) Workflow** betiğini kullanın. Bu betik, sunucu ayağa kalkana kadar bekler ve API anahtarlarınızı otomatik olarak test eder.

```python
import subprocess
import time
import requests
import sys

# MESA sunucusunu arka planda başlat
print("⏳ MESA API Sunucusu başlatılıyor...")
server_process = subprocess.Popen(
    ["uvicorn", "mesa_memory.api.server:app", "--host", "0.0.0.0", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

# Erken Müdahale (Pre-flight) Kontrol Döngüsü
max_retries = 15
base_url = "http://localhost:8000"
api_key = "local-dev-key"
headers = {"X-API-Key": api_key}

for i in range(max_retries):
    try:
        # 1. Healthcheck Kontrolü (Sunucu ayağa kalktı mı?)
        res = requests.get(f"{base_url}/health", timeout=2)
        
        if res.status_code == 200:
            print("✅ Sunucu başarıyla ayağa kalktı!")
            
            # 2. Yetkilendirme ve DB Kontrolü (401 ve 500 hatalarını erken yakalama)
            # Rastgele bir v3 endpoint'ine istek atarak auth ve db durumunu sınıyoruz
            auth_test = requests.get(f"{base_url}/v3/memory/status/0", headers=headers, timeout=2)
            
            if auth_test.status_code == 401:
                print("🚨 HATA (401): API Anahtarı yetkisiz! .env dosyasındaki MESA_API_KEY ile istekteki anahtar uyuşmuyor.")
                server_process.kill()
                sys.exit(1)
            elif auth_test.status_code == 500:
                print("🚨 HATA (500 UnknownError): İç sunucu hatası. Muhtemelen .kuzu veritabanı kilitlendi veya izin sorunu var.")
                print("ÇÖZÜM: Çalışma zamanını yeniden başlatın ve storage yolunu/izinlerini doğrulayın; mevcut veriyi silmeden önce backup ve recovery runbook'unu izleyin.")
                server_process.kill()
                sys.exit(1)
            else:
                print("✅ Yetkilendirme (Auth) ve Veritabanı bağlantıları başarılı!")
                break
                
        elif res.status_code >= 500:
             print(f"🚨 HATA ({res.status_code}): Sunucu çöktü. LLM_API_KEY veya veritabanı ayarlarını kontrol edin.")
             server_process.kill()
             sys.exit(1)
             
    except requests.exceptions.ConnectionError:
        print(f"[{i+1}/{max_retries}] Sunucu bekleniyor...")
        time.sleep(2)

else:
    print("❌ Sunucu belirtilen sürede başlatılamadı. Lütfen stdout/stderr loglarını kontrol edin.")
    server_process.kill()
```

## Adım 5: Python SDK ile Test Etme

Artık MESA sunucunuz çalıştığına göre, Colab hücresi üzerinden Python SDK'sını kullanarak sisteme hafıza ekleyebilir ve sorgulama yapabilirsiniz:

```python
from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client.client import MesaClient

# İstemciyi başlat (API anahtarı .env dosyasındaki MESA_API_KEY ile aynı olmalı)
client = MesaClient(base_url="http://localhost:8000", api_key="local-dev-key")

# 1. Hafıza Ekleme (Insert)
response = client.insert(MemoryInsertRequest(
    agent_id="colab_ajani_1",
    session_id="s1",
    content="Türkiye'nin başkenti Ankara'dır ve 1923 yılında başkent ilan edilmiştir."
))
print(f"Hafıza işlenmek üzere sıraya alındı. Log ID: {response.log_id}")

# 2. Arama Yapma (Search)
results = client.search(MemorySearchRequest(
    agent_id="colab_ajani_1",
    query="Türkiye'nin başkenti neresidir?",
    limit=5
))

print(f"\\nBulunan sonuç sayısı: {results.total}")
for r in results.results:
    print(f"- {r.entity_name} (Skor: {r.score:.4f})")
```

## Ek İpuçları ve Sorun Giderme

- **Ngrok Kullanımı:** Eğer MESA API'nize Colab dışından (örneğin yerel bilgisayarınızdaki bir Claude Desktop uygulamasından veya başka bir servisten) erişmek isterseniz `pyngrok` veya `localtunnel` kullanarak 8000 portunu dışarıya açabilirsiniz.
- **Veri Kalıcılığı (Persistence):** Colab ortamı geçicidir. Oturum kapandığında MESA'nın SQLite, LanceDB ve KuzuDB veritabanları silinir. Kalıcılık sağlamak için Google Drive'ınızı bağlayabilir ve veri dizinlerini Drive'a taşıyabilirsiniz:
  ```python
  from google.colab import drive
  drive.mount('/content/drive')
  ```
- **KuzuDB RAM Sınırları:** MESA'nın KuzuDB graf veritabanı oldukça performanslıdır ancak Colab'in ücretsiz sürümündeki 12GB RAM sınırlarına dikkat etmekte fayda vardır.

Tebrikler! MESA'yı Google Colab üzerinde başarıyla kurdunuz ve çalıştırdınız.
