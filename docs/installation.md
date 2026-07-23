# Installation and deployment

Bu rehber v3 lexical-core uyumluluk topology’si ile yayınlanmamış v4
full-cognitive topology’sini ayrı tutar. Repository `.env` dosyasını
kendiliğinden yüklemez. Gerçek secret’ları bir secret manager’da saklayın.

## Gereksinimler

- Python 3.10–3.13
- Kilitli kurulum için `uv`
- Compose kullanılıyorsa Docker Engine ve Docker Compose

```bash
git clone https://github.com/Yasou13/MESA.git
cd MESA
python -m pip install "uv==0.9.6"
uv sync --locked --extra dev
uv pip check
```

`pyproject.toml` desteklenen dependency aralıklarını, `uv.lock` CI/Docker
tarafından kullanılan tekrarlanabilir grafiği tanımlar. Model adapter’ları ve
benchmark bağımlılıkları yalnız gerekli profillerde kurulmalıdır.

## Ortak ortam ilkeleri

```bash
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
export MESA_LOAD_DOTENV=false
export MESA_LOG_LEVEL=INFO
export MESA_LOG_FORMAT=json
```

Storage root uygulamaya ait, yazılabilir ve açıkça seçilmiş bir dizin olmalıdır.
Repository, home veya filesystem root kullanmayın. V3 ve v4 için ayrı fiziksel
root kullanın.

## V4 combined runtime

V4’ün desteklenen storage-writing topology’si tek `combined` process’tir:

```bash
export MESA_RUNTIME_PROFILE=combined
export MESA_MODEL_ENABLED=true
export MESA_EXTERNAL_PROVIDER_ENABLED=true
export MESA_PRINCIPAL_ID=service-api
export MESA_PRINCIPAL_TYPE=SERVICE
export MESA_PRINCIPAL_STATUS=active
python -m mesa_memory.runtime_entrypoint
```

Provider’a özel adapter/model/credential değişkenleri deployment tarafından
sağlanır. Release adayı provider seçimi performans, maliyet ve güvenlik kanıtı
olmadan sabitlenmez. Model-disabled çalıştırma topology smoke testinde
kullanılabilir ancak full-cognitive production kanıtı değildir.

V4 credentials ve ACL:

```bash
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
mesa-v4-admin issue-key --principal service-api
mesa-v4-admin grant-role --principal service-api \
  --tenant tenant-a --role OWNER
mesa-v4-admin grant-agent --principal service-api \
  --agent agent-a --permission SESSION_CREATE
```

Üretilen `key_id.secret` credential’ı secret manager’a aktarın ve runtime’a
`MESA_API_KEY` olarak sağlayın. Plaintext yalnız oluşturma/rotate anında
gösterilir.

### V4 Compose

```bash
export MESA_API_KEY="$(secret-manager read mesa-v4-api-key)"
export MESA_PRINCIPAL_ID=service-api
export MESA_MODEL_ENABLED=true
export MESA_EXTERNAL_PROVIDER_ENABLED=true
docker compose -f docker-compose.v4.yml config --quiet
docker compose -f docker-compose.v4.yml up --build -d
docker compose -f docker-compose.v4.yml ps
curl --fail -H "X-API-Key: $MESA_API_KEY" \
  http://127.0.0.1:8000/health
```

`docker-compose.v4.yml` yalnız `mesa-v4` service’ini ve tek v4 volume’ünü
çalıştırır. Aynı volume’ü başka writer’a mount etmeyin.

## İlk v4 catalog ve session

ACL verildikten sonra `MesaV4Client` ile catalog oluşturulur:

```python
from mesa_client import MesaV4Client

with MesaV4Client("http://127.0.0.1:8000", api_key=credential) as client:
    client.create_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        tenant_name="Tenant A",
        workspace_name="Workspace A",
    )
    client.create_dataset(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        dataset_name="Dataset A",
    )
    session = client.start_session(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_ids=["dataset-a"],
        agent_id="agent-a",
    )
```

Session ID sunucu tarafından üretilir ve dataset kapsamı sonradan değişmez.

## V3 lexical-core compatibility

`docker-compose.yml`, ayrı `api-only` + `worker-only` process’lerle v3
lexical-core topology’sini korur:

```bash
export MESA_API_KEY="$(secret-manager read mesa-v3-api-key)"
export MESA_PRINCIPAL_ID=service-api
docker compose config --quiet
docker compose up --build -d
```

Process’leri elle başlatmak için:

```bash
MESA_RUNTIME_PROFILE=api-only python -m mesa_memory.runtime_entrypoint
MESA_RUNTIME_PROFILE=worker-only python -m mesa_memory.runtime_entrypoint
```

Bu topology v4 Graph V2, dataset ACL veya projection-ledger garantisi sunmaz.

## Migration, backup ve restore

Migration yalnız uygulama durmuşken ve release runbook onayıyla çalıştırılır:

```bash
alembic -c mesa_storage/alembic.ini upgrade head
```

Offline backup/restore:

```bash
mesa-recovery --trusted-root /srv/mesa backup \
  --source-root /srv/mesa/v4-data \
  --backup-root /srv/mesa/backups/v4-2026-07-23 \
  --stores-stopped
mesa-recovery --trusted-root /srv/mesa validate \
  --backup-root /srv/mesa/backups/v4-2026-07-23
mesa-recovery --trusted-root /srv/mesa restore \
  --backup-root /srv/mesa/backups/v4-2026-07-23 \
  --restore-root /srv/mesa/restore-v4-test
```

Restore her zaman yeni boş hedefe yapılır. V3 storage yerinde migrate edilmez;
backup sonrası ayrı v4 root’ta offline rebuild ve parity kontrolü yapılır.

## Yerel doğrulama

```bash
uv run ruff check .
uv run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client \
  --ignore-missing-imports --explicit-package-bases --follow-imports=skip
uv run pytest -q
uv run pytest -q mesa-benchmark/tests
```

V4 dar sözleşme paketi `tests/test_v4_*.py`, Graph V2 testleri ve
`tests/test_api_key_store.py` dosyalarını kapsar. Bu kontroller release kanıtı
için gereklidir fakat 24 saat soak kapısının yerine geçmez.
