Evet, bu fikir MESA’ya çok uygun. Ancak ZIP’teki mevcut yapıya göre planı biraz değiştirmek gerekiyor. Şu anda MESA MCP tarafı:

- yalnızca **stdio** çalışıyor,
- yalnızca **5 araç** sunuyor,
- doğrudan storage’a değil MESA HTTP API’sine bağlanıyor,
- hâlâ **V3 endpoint’lerini** kullanıyor,
- `actor_id`, namespace ve varsayılan project değerlerini ortam değişkenlerinden sabit alıyor,
- gerçek client bağlantısı, aktif oturum, aktivite geçmişi ve client politikası tutmuyor.

Yani dashboard’u mevcut MCP’nin üzerine yalnızca arayüz olarak koymak yeterli değil. Önce MCP katmanını **yönetilebilir bir gateway ve control plane** hâline getirmeliyiz.

# MESA Control Panel için nihai mimari

```
Codex ──────────────┐
Antigravity ────────┤
Cursor ─────────────┼── MESA MCP Gateway
Claude Code ────────┤        │
VS Code ────────────┘        │
                             ├── Client identity
                             ├── Policy engine
                             ├── Approval engine
                             ├── Activity recorder
                             ├── Rate limiter
                             └── Tool dispatcher
                                      │
                                      ▼
                                 MESA API V4
                                      │
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
                   SQLite          LanceDB           Kùzu
                                      │
                                      ▼
                             MESA Control Panel
```

Control Panel yalnızca metrik gösteren bir ekran olmamalı. Gerçek anlamda şu üç işi yapmalı:

1. **Gözlemleme:** Kim, ne zaman, hangi aracı kullandı?
2. **Politika yönetimi:** Hangi client ne yapabilir?
3. **Operasyon yönetimi:** Bekleyen yazma işlemini onayla, hafızayı düzenle, replay çalıştır, client bağlantısını kes.

# ZIP’te gördüğüm temel eksikler

## 1. MCP şu anda V3’e bağlı

`mesa_mcp/http_service.py` içindeki işlemler:

```
/v3/health
/v3/memory/insert
/v3/memory/search
/v3/memory/records/{id}
```

üzerinden ilerliyor.

Bu nedenle mevcut MCP:

- V4 tenant/workspace/dataset modelini kullanmıyor,
- principal kimliği taşımıyor,
- mutation ID ve pipeline run bilgisini göremiyor,
- projection durumlarını takip edemiyor,
- rollback/replay gibi V4 kabiliyetlerinden yararlanmıyor.

Control Panel geliştirilirken MCP gateway’in doğrudan **V4-native** hâle getirilmesi gerekir.

## 2. Client kimliği gerçek değil

Mevcut ayarlarda:

```
actor_id = "antigravity-agent"
namespace = "local"
default_project_id = "mesa"
```

değerleri process başına sabit.

Aynı gateway’e Codex ve Antigravity bağlandığında ikisini ayıramazsın. Her bağlantının ayrı kimliği olmalı:

```
client_id
client_instance_id
connection_id
principal_id
tenant_id
workspace_id
dataset_id
project_id
session_id
```

## 3. Activity kaydı yok

Şu anda tool çağrıları sadece stderr log’una düşebilir. Dashboard için sorgulanabilir ve kalıcı bir activity/audit kaydı bulunmuyor.

## 4. Onay mekanizması yok

`mesa_store_memory` çağrısı geldiğinde ya doğrudan çalışıyor ya hata veriyor. “Kullanıcı onayı bekliyor” durumu bulunmuyor.

## 5. Araç politikaları process seviyesinde

Read, write, delete veya auto-context gibi yetkiler client bazlı yönetilemiyor.

---

# Planındaki önemli düzeltmeler

## Bağlantı ile client’ı ayırmalıyız

`mcp_clients` tablosunda `last_seen_at` tutmak tek başına yeterli değil. Bir client’ın aynı anda birden fazla IDE penceresi veya cihazı olabilir.

Bunun yerine:

```
mcp_clients
    └── mcp_connections
            └── mcp_tool_calls
```

olmalı.

Örneğin:

```
Client: Codex
 ├── Connection: VS Code window A
 ├── Connection: CLI session
 └── Connection: Remote development container
```

Böylece “Codex açık mı?” ile “hangi Codex oturumu açık?” ayrılabilir.

## Activity ile audit’i ayırmalıyız

İki farklı tablo gerekir:

- **Activity:** yüksek hacimli tool çağrıları ve performans verisi
- **Audit:** politika değişiklikleri, hafıza silme, onay, credential oluşturma gibi güvenlik olayları

Her search çağrısını audit log’a yazmak gereksiz yük oluşturur. Fakat bir kullanıcının bir client’a delete izni vermesi mutlaka audit kaydı olmalıdır.

## Boolean kolonlar yerine politika modeli kullanmalıyız

Şu yaklaşım ilk aşamada kolay:

```
read_enabled
write_enabled
delete_enabled
auto_context_enabled
```

Ancak yeni araç geldikçe migration gerektirir.

Daha esnek model:

```
{
  "tools": {
    "mesa_health": "allow",
    "mesa_recall": "allow",
    "mesa_remember": "require_approval",
    "mesa_forget": "deny"
  },
  "features": {
    "automatic_context": true,
    "automatic_save": false,
    "graph_processing": true,
    "vector_indexing": true
  }
}
```

SQL’de JSON saklanabilir veya normalize edilmiş politika kuralları kullanılabilir.

MESA için normalize edilmiş kurallar daha doğru:

```
mcp_policy_rules
- subject_type
- subject_id
- operation
- effect
- conditions_json
- priority
```

Böylece global, client, project ve dataset politikaları aynı motorla yönetilir.

---

# Önerdiğim veri modeli

## 1. MCP client kayıtları

```
CREATE TABLE mcp_clients (
    client_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    client_type TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,

    default_tenant_id TEXT,
    default_workspace_id TEXT,
    default_dataset_id TEXT,
    default_project_id TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,

    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

`client_type` örnekleri:

```
antigravity
codex
cursor
claude_code
vscode
manual
unknown
```

## 2. Aktif ve geçmiş bağlantılar

```
CREATE TABLE mcp_connections (
    connection_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,

    transport TEXT NOT NULL,
    status TEXT NOT NULL,

    connected_at TEXT NOT NULL,
    disconnected_at TEXT,
    last_seen_at TEXT NOT NULL,

    remote_address_hash TEXT,
    protocol_version TEXT,
    client_version TEXT,
    user_agent TEXT,

    session_id TEXT,
    project_id TEXT,

    FOREIGN KEY (client_id) REFERENCES mcp_clients(client_id)
);
```

Durumlar:

```
CONNECTED
IDLE
DISCONNECTED
REVOKED
ERROR
```

Burada gerçek bir fiziksel “Disconnect” her transportta mümkün olmayabilir. Bu yüzden dashboard’daki düğme teknik olarak:

1. connection’ı `REVOKED` yapar,
2. sonraki çağrıyı reddeder,
3. destekleniyorsa aktif stream’i sonlandırır.

## 3. Tool activity

```
CREATE TABLE mcp_tool_calls (
    call_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    connection_id TEXT,
    client_id TEXT NOT NULL,
    principal_id TEXT,

    tenant_id TEXT,
    workspace_id TEXT,
    dataset_id TEXT,
    project_id TEXT,
    session_id TEXT,

    tool_name TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    status TEXT NOT NULL,

    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,

    request_size_bytes INTEGER,
    response_size_bytes INTEGER,
    request_summary TEXT,
    request_fingerprint TEXT,

    memory_id TEXT,
    mutation_id TEXT,
    pipeline_run_id TEXT,

    vector_status TEXT,
    graph_status TEXT,

    error_code TEXT,
    error_message TEXT,

    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

`decision`:

```
ALLOW
DENY
REQUIRE_APPROVAL
RATE_LIMIT
```

`status`:

```
PENDING
SUCCESS
FAILED
DENIED
CANCELLED
TIMEOUT
```

Burada tam prompt varsayılan olarak tutulmamalı.

Saklanması gerekenler:

- güvenli özet,
- hash/fingerprint,
- byte uzunluğu,
- memory/mutation kimliği,
- proje ve client bilgileri.

## 4. Politika kuralları

```
CREATE TABLE mcp_policy_rules (
    rule_id TEXT PRIMARY KEY,

    scope_type TEXT NOT NULL,
    scope_id TEXT,

    operation TEXT NOT NULL,
    effect TEXT NOT NULL,

    priority INTEGER NOT NULL DEFAULT 100,
    conditions_json TEXT NOT NULL DEFAULT '{}',

    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Scope örnekleri:

```
GLOBAL
CLIENT
PROJECT
DATASET
MEMORY_TYPE
```

Operation örnekleri:

```
HEALTH
READ
SEARCH
CONTEXT
WRITE
UPDATE
DELETE
REPLAY
ROLLBACK
GRAPH_PROCESS
VECTOR_INDEX
VIEW_RAW_CONTENT
```

Effect:

```
ALLOW
DENY
REQUIRE_APPROVAL
```

Politika önceliği:

```
Explicit deny
    ↓
Client/project-specific rule
    ↓
Dataset rule
    ↓
Global default
```

En güvenli varsayılan:

```
Health: allow
Read: allow
Search: allow
Context: allow
Write: require approval
Update: require approval
Delete: require approval
Rollback: deny
Raw content view: require approval/admin
```

## 5. Onay kuyruğu

Bu planın eksik olan en önemli parçası bu.

```
CREATE TABLE mcp_approval_requests (
    approval_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,

    client_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,

    request_summary TEXT NOT NULL,
    payload_encrypted BLOB,
    payload_hash TEXT NOT NULL,

    requested_at TEXT NOT NULL,
    expires_at TEXT,

    decided_at TEXT,
    decided_by TEXT,
    decision_reason TEXT,

    execution_status TEXT,
    executed_at TEXT
);
```

Durumlar:

```
PENDING
APPROVED
DENIED
EXPIRED
CANCELLED
EXECUTED
FAILED
```

Akış:

```
mesa_remember çağrısı
        ↓
Politika: REQUIRE_APPROVAL
        ↓
Payload doğrulanır
        ↓
Approval request oluşturulur
        ↓
MCP istemcisine PENDING_APPROVAL döner
        ↓
Dashboard'da kullanıcı onaylar
        ↓
Gateway aynı doğrulanmış payload'ı çalıştırır
        ↓
Memory/mutation oluşturulur
```

MCP cevabı:

```
{
  "status": "PENDING_APPROVAL",
  "approval_id": "apr_01...",
  "message": "Memory write requires user approval."
}
```

Onay sonrasında istemcinin sonucu öğrenebilmesi için:

```
mesa_get_approval_status
```

aracı eklenebilir.

## 6. Sistem ayarları

```
CREATE TABLE control_plane_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
```

Temel ayarlar:

```
{
  "memory.enabled": true,
  "mcp.enabled": true,
  "writes.default_policy": "require_approval",
  "deletes.default_policy": "require_approval",
  "activity.retention_days": 30,
  "activity.store_raw_payload": false,
  "approval.default_expiry_minutes": 30,
  "graph.processing_enabled": true,
  "vector.indexing_enabled": true
}
```

# Manuel yönetim sistemi

Senin talebindeki en önemli nokta bu. Her otomatik özelliğin elle yönetilebildiği bir karşılığı olmalı.

## Global kontroller

```
MCP Gateway                 ON / OFF
Memory reads                ON / OFF
Memory writes               ON / OFF
Context retrieval           ON / OFF
Automatic memory writes     ON / OFF
Vector indexing             ON / OFF
Graph processing            ON / OFF
Background enrichment       ON / OFF
Raw content display         ON / OFF
```

`MCP Gateway OFF` ile `Memory OFF` aynı şey olmamalı:

- Gateway kapalı: MCP bağlantıları kabul edilmez.
- Memory kapalı: Gateway ayakta kalır fakat memory araçları kontrollü hata verir.
- Writes kapalı: read/search/context çalışır.
- Graph kapalı: mutation kaydedilir fakat graph projection bekletilebilir veya atlanır.
- Vector kapalı: lexical retrieval devam eder.

## Client bazında kontroller

Her client için:

```
Enabled
Read
Search
Context
Write
Update
Delete
Replay
Rollback
View raw content
Automatic context
Automatic store
Graph processing
Vector indexing
```

Ayrıca:

```
Default project
Default dataset
Allowed projects
Allowed memory types
Maximum results
Maximum write size
Hourly read limit
Hourly write limit
Approval policy
```

## Tek çağrı bazında kontrol

Activity ekranından kullanıcı:

- çağrıyı tekrar çalıştırabilir,
- aynı client’ı engelleyebilir,
- memory kaydını açabilir,
- mutation durumunu görebilir,
- başarısız projection’ı replay edebilir,
- çağrının güvenli payload özetini görebilir,
- policy kuralı oluşturabilir.

Örneğin:

```
Codex bu memory type'ı tekrar yazamasın
```

dediğinde dashboard şunu üretir:

```
scope_type: CLIENT
scope_id: codex
operation: WRITE
condition:
  memory_type: task
effect: DENY
```

---

# Tool tasarımı

Mevcut düşük seviyeli araçlar kalabilir:

```
mesa_health
mesa_store_memory
mesa_search_memory
mesa_get_memory
mesa_get_context
```

Ancak bunlar legacy/advanced yüzey olarak görülmeli.

Üst seviye araçlar:

## `mesa_remember`

`mesa_store_memory` üzerine kullanıcı dostu wrapper.

```
{
  "content": "RBAC'te WRITE yetkisi READ'i de kapsar.",
  "memory_type": "decision",
  "project_id": "mesa",
  "importance": 0.8
}
```

Gateway:

- secret taraması,
- policy kontrolü,
- duplicate kontrolü,
- onay kontrolü,
- V4 mutation oluşturma

işlerini yapar.

## `mesa_recall`

Search ve context arasında daha doğal araç:

```
{
  "query": "RBAC kararlarımız nelerdi?",
  "project_id": "mesa",
  "mode": "context",
  "limit": 8
}
```

Modlar:

```
search
context
exact
```

## `mesa_improve`

Mevcut memory’yi sessizce overwrite etmemeli.

```
old memory
    ↓
new revision/mutation
    ↓
old status = superseded
```

İstek:

```
{
  "memory_id": "mem_...",
  "new_content": "...",
  "reason": "Architecture decision changed"
}
```

Varsayılan politika: `REQUIRE_APPROVAL`.

## `mesa_forget`

Hard delete yerine seçenekli olmalı:

```
{
  "memory_id": "mem_...",
  "mode": "deprecate",
  "reason": "No longer valid"
}
```

Modlar:

```
deprecate
archive
purge
```

- `deprecate`: retrieval sıralamasından çıkarır ama geçmişi korur.
- `archive`: normal sorgularda göstermez.
- `purge`: fiziksel silme süreci başlatır ve kesin onay ister.

Ek yönetim araçları:

```
mesa_get_approval_status
mesa_cancel_approval
mesa_report_usage
```

Ancak client’lara control-panel yönetim araçları verilmemeli. Örneğin client kendi write yetkisini açamamalı.

---

# Ortak middleware zinciri

Bütün araçlar tek bir işlem hattından geçmeli:

```
Request received
      ↓
Parse transport identity
      ↓
Resolve client
      ↓
Resolve principal and catalog scope
      ↓
Record call start
      ↓
Check global state
      ↓
Check connection state
      ↓
Evaluate policy
      ↓
Check rate limits
      ↓
Validate and sanitize payload
      ↓
Approval required?
   ├── Yes → persist approval → return pending
   └── No
      ↓
Execute MESA V4 operation
      ↓
Record mutation/pipeline IDs
      ↓
Shape safe response
      ↓
Record completion and metrics
```

Bunu her araçta tekrar yazmamalıyız.

Önerilen paket yapısı:

```
mesa_mcp/
├── gateway/
│   ├── application.py
│   ├── transport.py
│   ├── dispatcher.py
│   ├── identity.py
│   └── middleware.py
│
├── tools/
│   ├── memory_tools.py
│   ├── lifecycle_tools.py
│   └── schemas.py
│
├── policy/
│   ├── engine.py
│   ├── models.py
│   └── repository.py
│
├── approvals/
│   ├── service.py
│   └── repository.py
│
├── activity/
│   ├── recorder.py
│   ├── queries.py
│   └── retention.py
│
├── service/
│   ├── protocol.py
│   ├── v3_adapter.py
│   └── v4_adapter.py
│
└── server.py
```

Mevcut `server.py` içindeki handler sözlüğü giderek büyütülmemeli.

# Control Panel backend

MESA ana FastAPI uygulamasına yeni router eklenebilir:

```
mesa_control/
├── router.py
├── schemas.py
├── service.py
├── repository.py
└── security.py
```

Mount:

```
app.include_router(control_router, prefix="/control")
```

Admin endpoint’leri normal memory API key’iyle kullanılmamalı. Ayrı admin scope gerektirir.

## Önerilen endpoint’ler

### Overview

```
GET /control/overview
GET /control/metrics/timeseries
GET /control/system/health
```

### Clients

```
GET    /control/mcp/clients
POST   /control/mcp/clients
GET    /control/mcp/clients/{client_id}
PATCH  /control/mcp/clients/{client_id}
POST   /control/mcp/clients/{client_id}/revoke
POST   /control/mcp/clients/{client_id}/rotate-credential
```

### Connections

```
GET  /control/mcp/connections
GET  /control/mcp/connections/{connection_id}
POST /control/mcp/connections/{connection_id}/disconnect
POST /control/mcp/connections/{connection_id}/revoke
```

### Activity

```
GET /control/activity
GET /control/activity/{call_id}
POST /control/activity/{call_id}/retry
```

### Approvals

```
GET  /control/approvals
GET  /control/approvals/{approval_id}
POST /control/approvals/{approval_id}/approve
POST /control/approvals/{approval_id}/deny
POST /control/approvals/{approval_id}/cancel
```

### Memories

```
GET    /control/memories
GET    /control/memories/{memory_id}
PATCH  /control/memories/{memory_id}
POST   /control/memories/{memory_id}/deprecate
POST   /control/memories/{memory_id}/restore
DELETE /control/memories/{memory_id}
GET    /control/memories/{memory_id}/usage
GET    /control/memories/{memory_id}/lineage
```

### Mutations and processing

```
GET  /control/mutations
GET  /control/mutations/{mutation_id}
POST /control/mutations/{mutation_id}/replay
POST /control/mutations/{mutation_id}/rollback

GET  /control/processing/queue
POST /control/processing/jobs/{job_id}/retry
POST /control/processing/jobs/{job_id}/cancel
```

### Policies

```
GET    /control/policies
POST   /control/policies
PATCH  /control/policies/{rule_id}
DELETE /control/policies/{rule_id}
POST   /control/policies/simulate
```

`simulate` endpoint’i çok değerli:

```
{
  "client_id": "codex",
  "operation": "WRITE",
  "project_id": "mesa",
  "memory_type": "decision"
}
```

Cevap:

```
{
  "decision": "REQUIRE_APPROVAL",
  "matched_rule_id": "rule_...",
  "reason": "Client write policy requires approval"
}
```

### Settings

```
GET   /control/settings
PATCH /control/settings
```

---

# Dashboard ekranları

## 1. Overview

Ana kartlar:

```
Active clients
Active connections
MCP calls today
Memory reads
Context retrievals
Writes
Pending approvals
Denied operations
Failed operations
Average latency
P95 latency
Projection backlog
Dead-letter jobs
```

Alt bölümler:

- son 20 işlem,
- bekleyen onaylar,
- hata oranı,
- en aktif client’lar,
- en çok kullanılan projeler,
- projection sağlığı.

Burada yalnızca ortalama latency göstermek yeterli değil. P50, P95 ve P99 da gösterilmeli; yeterli örnek yoksa percentile gösterilmemeli.

## 2. MCP Connections

Client kartı:

```
Antigravity
● Connected — 2 active sessions

Principal: prn_antigravity
Default project: MESA
Transport: HTTP
Connected since: 10:31
Last activity: 12 seconds ago
Calls today: 86
Errors: 1
Pending approvals: 2

Memory access       ON
Read                ON
Context             ON
Write               APPROVAL
Delete              OFF
Automatic context   ON
Automatic save      OFF

[Connections] [Activity] [Policies] [Revoke]
```

## 3. Activity

Filtreler:

```
Client
Connection
Tool
Operation
Decision
Status
Project
Dataset
Memory type
Date range
Latency range
```

Satıra tıklayınca:

- tool adı,
- safe request summary,
- policy sonucu,
- eşleşen rule,
- memory/mutation/pipeline bağlantısı,
- latency breakdown,
- projection sonuçları,
- hata ayrıntısı,
- retry düğmesi.

Latency breakdown:

```
Policy evaluation      2 ms
Approval check         1 ms
MESA API              41 ms
SQL projection         8 ms
Vector projection     19 ms
Graph projection      27 ms
Total                 74 ms
```

Asenkron projection varsa toplam çağrı süresiyle projection süresi ayrı gösterilmeli.

## 4. Approvals

Bu ekran MVP’ye kesinlikle dahil edilmeli.

Kart:

```
Codex wants to store a memory

Type: decision
Project: MESA
Summary: “V4 retrieval must authorize datasets before search.”
Risk: Normal
Requested: 15 seconds ago
Expires: 29 minutes

[View content] [Approve once] [Always allow this type] [Deny]
```

“Always allow this type” seçimi hem onay verir hem policy kuralı oluşturur.

## 5. Memory Explorer

Liste kolonları:

```
Memory
Type
Project
Dataset
Created by
Source client
Status
Confidence
Vector
Graph
Uses
Last used
Created
```

Detay:

- current content,
- previous revisions,
- provenance,
- source chunk,
- creator client,
- mutation lineage,
- vector projection,
- graph entities/edges,
- retrieval history,
- superseded-by ilişkisi,
- deprecate/archive/purge işlemleri.

Raw içerik açılması ayrı izin gerektirmeli.

## 6. Policies

Hem form tabanlı hem gelişmiş JSON görünümü olabilir.

Basit politika editörü:

```
Subject: Codex
Operation: Write memory
Project: MESA
Memory types: decision, architecture
Decision: Require approval
Limit: 20/hour
```

## 7. Processing Queue

Bu ekranı tamamen ikinci aşamaya bırakmak yerine MVP’de salt-okunur hâlde göstermek faydalı olur:

- waiting,
- leased,
- retrying,
- failed,
- dead-letter,
- completed.

İlk sürümde yalnızca retry düğmesi eklenebilir.

---

# “MESA gerçekten kullanıldı mı?” göstergesi

Ana ekranda sadece call sayısı değil, anlamlı kullanım kanıtı gösterilmeli:

```
Antigravity used MESA 43 times today

18 context retrievals
11 searches
 5 memories stored
 2 writes denied
 7 health checks

Last successful context injection:
Project: MESA
Task: MCP dashboard architecture
3 memories returned
612 estimated tokens
14 seconds ago
```

Ayrıca bir **usage funnel** eklenebilir:

```
Task started
    ↓ 31
Context requested
    ↓ 24
Memories returned
    ↓ 21
Memory referenced
    ↓ 13
New memory stored
      7
```

“Memory referenced” bilgisini tam belirlemek zor olabilir. MCP client geri bildirim göndermiyorsa en azından “context response delivered” olarak ölçülmeli; kullanılan bilgiyle delivered bilgi birbirine karıştırılmamalı.

# V4 ile doğru entegrasyon

MCP tarafında şu scope çözümlemesi yapılmalı:

```
client credential
      ↓
principal_id
      ↓
tenant
      ↓
workspace
      ↓
dataset
      ↓
project/session
```

`project_id`, güvenlik sınırı olarak kullanılmamalı. UI etiketi olabilir ama altında gerçek V4 dataset bulunmalı.

Örnek mapping:

```
Project: MESA
Tenant: yasin
Workspace: development
Dataset: mesa-project-memory
Session: mcp-antigravity-mesa
```

Bu mapping `mcp_project_bindings` tablosunda tutulabilir:

```
CREATE TABLE mcp_project_bindings (
    binding_id TEXT PRIMARY KEY,
    client_id TEXT,
    external_project_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(client_id, external_project_id)
);
```

Bu olmadan farklı IDE’lerde aynı `project_id` yanlış dataset’e bağlanabilir.

# Güvenlik kararları

## Credential

Her client’a ayrı credential:

```
Antigravity credential
Codex credential
Cursor credential
```

Aynı API key paylaşılmamalı.

Credential’ın raw değeri veritabanında tutulmamalı:

- key ID,
- hash,
- oluşturulma zamanı,
- son kullanım,
- revoke durumu.

## İçerik gizliliği

Varsayılan:

```
request_summary: stored
full_content: not stored
content_hash: stored
```

Onay gerektiren işlemlerde payload’ın daha sonra yürütülmesi gerekir. Bu durumda payload:

- şifrelenmiş olarak,
- kısa retention süresiyle,
- onay bitince silinecek şekilde

saklanmalı.

## Prompt injection

Mevcut MESA MCP secret taraması iyi bir başlangıç. Buna ek olarak activity dashboard’da:

- injection suspect,
- secret suspect,
- oversized request,
- unusual write rate

işaretleri gösterilebilir.

Ancak mevcut sistemde olduğu gibi prompt injection eşleşmesi otomatik hard block olmamalı; politika ile yönetilebilir olmalı.

# Uygulama aşamaları

## Aşama 1 — Control plane çekirdeği

- MCP client registry
- connection registry
- activity recorder
- global/client policy motoru
- approval queue
- migrations
- admin API
- mevcut stdio server’a middleware entegrasyonu

Bu aşamada HTTP gateway zorunlu değil. Önce Antigravity stdio bağlantısı bile dashboard’da görülebilir.

Stdio process başlarken:

```
connection register
heartbeat/activity update
disconnect on process exit
```

yapar.

## Aşama 2 — V4-native MCP

- V4 service adapter
- principal ve dataset binding
- mutation/pipeline ID takibi
- `remember/recall/improve/forget`
- replay/rollback bağlantıları
- typed retrieval sonuçları

Bu aşamada V3 araçlar compatibility olarak kalabilir.

## Aşama 3 — Web dashboard MVP

- Overview
- Connections
- Activity
- Approvals
- Memories
- Settings

Ben **Approvals ekranını MVP’ye ekliyorum**. Çünkü “write kullanıcı onaylı” kararının arayüzü olmadan politika kullanışsız kalır.

## Aşama 4 — Merkezi HTTP MCP Gateway

- çoklu istemci bağlantısı
- connection lifecycle
- credential-based client identity
- heartbeat
- revocation
- shared backend
- transport abstraction

Stdio da kaldırılmamalı:

```
stdio → local bridge → central MCP gateway
```

veya standalone mod olarak korunmalı.

## Aşama 5 — Gelişmiş operasyon

- graph explorer
- pipeline/queue yönetimi
- policy simulator
- anomaly detection
- retention controls
- export/import
- detailed latency tracing
- alerts

# MVP’de kesin olması gerekenler

İlk sürüm için nihai kapsamım:

```
1. Overview
2. Clients and Connections
3. Activity
4. Pending Approvals
5. Memory Explorer
6. Global and client policies
7. Manual enable/disable controls
8. V4 project/dataset bindings
9. Read/write/delete approval modes
10. Safe audit trail
```

İlk sürümde olmayanlar:

```
Tam graph visualization
Gelişmiş analytics
Anomaly detection
Çok ayrıntılı cost accounting
Karmaşık workflow designer
```

# Kritik mimari karar

Control Panel mantığını `MemoryDAO` içine eklememeliyiz.

Aksi takdirde zaten büyük olan DAO daha da büyür. Ayrı repository ve service katmanı kurulmalı:

```
ControlRepository
PolicyRepository
ActivityRepository
ApprovalRepository
ConnectionRepository
```

Bunlar aynı SQLite veritabanını kullanabilir fakat `MemoryDAO` üzerinden geçmemeli.

# Son hâliyle ürün tanımı

Bu geliştirmeden sonra MESA Control Panel şu olur:

> **MESA’ya bağlanan yapay zekâ istemcilerinin hafıza erişimini merkezi olarak gözlemleyen, yetkilendiren, onaylayan ve gerektiğinde manuel olarak yöneten bir memory operations console.**

Bu yalnızca Cognee benzeri bir MCP bağlantı ekranı değil; MESA’nın V4 mutation, provenance, replay, rollback ve projection altyapısını görünür ve yönetilebilir yapan gerçek bir kontrol düzlemi olur.

Benim bu plan için puanım:

- İlk gönderdiğin hâli: **8/10**
- Yukarıdaki connection/policy/approval/V4 ayrımlarıyla: **9.3/10**

En kritik ekleme **approval queue**, en kritik düzeltme ise **MCP’nin V3 ve sabit actor yapısından V4 principal/dataset modeline geçirilmesi**.