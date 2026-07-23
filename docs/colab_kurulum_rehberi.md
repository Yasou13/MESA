# MESA Google Colab geliştirme rehberi

Colab yalnız geliştirme, demo ve model deneyi içindir. Ephemeral disk,
tek-notebook process modeli ve secret yönetimi nedeniyle production-benzeri v4
deployment, migration veya soak kanıtı üretmez.

## 1. Kurulum

```python
!git clone https://github.com/Yasou13/MESA.git
%cd MESA
!python -m pip install "uv==0.9.6"
!uv sync --locked --extra dev --extra adapters
```

REBEL veya yerel transformer gerekiyorsa ayrıca `--extra ml` kullanın. Model
download’u GPU/RAM/disk kotasını aşabilir.

## 2. Secret ve runtime ayarları

Secret’ları notebook metnine veya repository `.env` dosyasına yazmayın. Colab
Secrets panelinden okuyun:

```python
import os
from google.colab import userdata

os.environ["MESA_API_KEY"] = userdata.get("MESA_API_KEY")
os.environ["LLM_API_KEY"] = userdata.get("LLM_API_KEY")
os.environ["MESA_PRINCIPAL_ID"] = "colab-principal"
os.environ["MESA_RUNTIME_PROFILE"] = "combined"
os.environ["MESA_STORAGE_ROOT"] = "/content/mesa-v4-data"
os.environ["MESA_LOAD_DOTENV"] = "false"
os.environ["MESA_MODEL_ENABLED"] = "true"
os.environ["MESA_EXTERNAL_PROVIDER_ENABLED"] = "true"
```

Provider adapter/model değişkenlerini kullandığınız provider’a göre ayrıca
tanımlayın. Bunları notebook çıktısında göstermeyin.

## 3. V4 credential ve ACL

Colab’daki geçici policy DB için:

```python
!uv run mesa-v4-admin issue-key --principal colab-principal
!uv run mesa-v4-admin grant-role --principal colab-principal \
  --tenant colab-tenant --role OWNER
!uv run mesa-v4-admin grant-agent --principal colab-principal \
  --agent colab-agent --permission SESSION_CREATE
```

`issue-key` çıktısı yalnız bir kez gösterilir. Üretilen credential’ı Colab
Secrets paneline aktarın; notebook’u paylaşmadan önce output’u temizleyin.

## 4. Sunucuyu başlatma

```python
import os
import subprocess

server = subprocess.Popen(
    ["uv", "run", "python", "-m", "mesa_memory.runtime_entrypoint"],
    env=os.environ.copy(),
)
```

Health kontrolü:

```python
import requests

headers = {"X-API-Key": os.environ["MESA_API_KEY"]}
response = requests.get(
    "http://127.0.0.1:8000/health", headers=headers, timeout=10
)
response.raise_for_status()
response.json()
```

Notebook hücresini yeniden çalıştırmadan önce eski process’i
`server.terminate()` ile kapatın. Aynı storage root’a ikinci combined process
başlatmayın.

## 5. Catalog, session ve mutation

```python
from mesa_client import MesaV4Client

client = MesaV4Client(
    "http://127.0.0.1:8000",
    api_key=os.environ["MESA_API_KEY"],
)
client.create_workspace(
    tenant_id="colab-tenant",
    workspace_id="colab-workspace",
    tenant_name="Colab Tenant",
    workspace_name="Colab Workspace",
)
client.create_dataset(
    tenant_id="colab-tenant",
    workspace_id="colab-workspace",
    dataset_id="colab-dataset",
    dataset_name="Colab Dataset",
)
session = client.start_session(
    tenant_id="colab-tenant",
    workspace_id="colab-workspace",
    dataset_ids=["colab-dataset"],
    agent_id="colab-agent",
)
accepted = client.insert(
    session_id=session["session_id"],
    dataset_id="colab-dataset",
    document_id="demo-doc",
    revision_id="revision-1",
    chunk_id="chunk-1",
    source_ref="colab://demo",
    content="Exact demo source",
)
client.wait_until_committed(accepted["mutation_id"])
```

## 6. Test

```python
!uv run pytest -q \
  tests/test_v4_api_contract.py \
  tests/test_v4_projection_integration.py \
  tests/test_graph_v2_identity.py \
  tests/test_v4_rrf_ablation.py
```

Colab kapanınca local storage kaybolabilir. Veri saklamak gerekiyorsa offline
backup alın; Google Drive’ı çalışan v4 storage root olarak kullanmayın.
