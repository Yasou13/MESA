# MESA operations runbook

Bu runbook v4 full-cognitive release candidate için kanonik operasyon
rehberidir. Production kararı Final MVP Certification tamamlanana kadar
`NO-GO`dur. V3 lexical-core uyumluluk işlemleri ayrıca belirtilir.

## 1. Desteklenen v4 topology

V4 aynı storage root’a yazan tek bir `combined` process ile çalışır. API
admission, durable consumer, validation ve SQLite/LanceDB/Kùzu projection
işleri bu process’tedir.

```bash
export MESA_RUNTIME_PROFILE=combined
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
export MESA_LOAD_DOTENV=false
# Provision credentials and reviewed model/provider settings before startup.
.venv/bin/python -m mesa_memory.runtime_entrypoint
```

Bu komut tek başına V4 provider kurulumu değildir. Canonical insert için
`MESA_MODEL_ENABLED=true`, kullanılabilir extraction/embedding ve seçili
validation policy gerekir; [kurulum](getting-started.md#installation) ve
[yapılandırma](configuration.md) ön koşullarını tamamlayın.

Aynı storage root’u ikinci API, worker veya combined process’e yazılabilir
olarak mount etmeyin. Yatay ölçekleme öncesi storage ownership protokolü
gerekir. V3 ile v4 farklı fiziksel storage root kullanmalıdır.

<a id="credential-bootstrap"></a>

## 2. İlk credential ve ACL kurulumu

Uygulama durdurulmuşken veya policy DB’yi kullanan tek operator olarak:

```bash
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
mesa-v4-admin issue-key --principal service-api
mesa-v4-admin grant-role --principal service-api \
  --tenant tenant-a --role OWNER
mesa-v4-admin grant-agent --principal service-api \
  --agent agent-a --permission SESSION_CREATE
```

İlk komut credential’ı yalnız bir kez stdout’a yazar. Secret manager’a aktarın;
log, shell history veya dokümana koymayın. Rotate edilen eski key aynı işlemde
revoke edilir:

```bash
mesa-v4-admin rotate-key --key-id KEY_ID
mesa-v4-admin revoke-key --key-id KEY_ID
```

Purge/rollback rol ile örtük gelmez:

```bash
mesa-v4-admin grant-dataset-permission --principal service-api \
  --tenant tenant-a --dataset dataset-a --permission ROLLBACK
```

## 3. Projection backlog ve DLQ

Önce `/health` ile `status`, `maintenance`, `rebuild_state` özeti ve
`/health/init` ile readiness kontrol edilir. `/health` HTTP 200 dönse de
`status` degraded olabilir. Aşağıdaki ayrıntılı sayaçlar `/metrics` üzerinden
izlenir; public `/health` yanıtında ayrı alanlar değildir:

- `mesa_v4_projection_backlog`
- `mesa_v4_projection_dead_letter`
- `mesa_v4_projection_stuck_leases`
- `mesa_v4_cleanup_backlog`
- `mesa_v4_cleanup_blocked`
- `mesa_v4_orphan_registry`
- `mesa_v4_shared_artifacts`

Backlog artıyorsa process loglarında mutation/pipeline run ID, failure class ve
lane aranır. İçerik, credential veya claim token loglanmamalıdır. Geçici hata
bounded retry ile ilerler; permanent/poison hata DLQ’ya gider.

Replay’den önce kök neden giderilir ve mutation’ın aynı tenant/dataset
kapsamında olduğu doğrulanır. Yetkili API çağrısı:

```bash
curl --fail -X POST \
  -H "X-API-Key: $MESA_API_KEY" \
  "http://127.0.0.1:8000/v4/mutations/$MUTATION_ID/replay"
```

Fenced lease nedeniyle claim token’ı kaybetmiş worker sonucu finalize edemez.
Lease’e elle müdahale etmek yerine process’i yeniden başlatın ve reconciler’ın
expired claim’i almasını bekleyin.

## 4. Rollback ve BLOCKED

Commit edilmemiş başarısız run rollback’i önce SQLite retrieval tombstone’u,
sonra vector/graph cleanup outbox’ı oluşturur. Yalnız ilgili
`artifact_sources` sahipliği kaldırılır. Başka aktif sahibi olan ortak artifact
silinmez.

Cleanup tamamlanamazsa run `BLOCKED` olur; bunu başarılı saymayın. Store
erişimini düzeltin, health parity’yi kontrol edin ve idempotent cleanup/replay
akışını yeniden çalıştırın. Fiziksel store’da manuel silme yapmayın; ledger ile
store daha fazla ayrışır.

## 5. Reconciliation

Reconciliation tenant ve dataset sınırında iki yönlüdür:

- ledger’da olup store’da olmayan artifact yeniden project edilir;
- store’da olup ledger/owner kaydı olmayan artifact karantinaya alınır ve
  cleanup kuyruğuna taşınır.

Release veya cutover öncesi projection backlog, blocked cleanup ve ownerless
orphan sıfır olmalıdır. Shared artifact sayısının sıfır olması gerekmez; ortak
sahiplik beklenen bir durumdur.

## 6. Backup, restore ve v4 rebuild

Uygulamayı durdurun ve kaynak storage’ı offline hale getirin:

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

`--stores-stopped` geriye dönük uyumluluk için açık bir operator
precondition'ıdır; tek başına consistency kanıtı değildir. Backup
implementation'ı ayrıca storage-root writer lock'ını doğrular/alır, her SQLite
dosyasını SQLite'ın online backup primitive'iyle ayrı bir tutarlı snapshot
olarak kopyalar ve ancak staging doğrulamasından sonra atomik publish yapar.
SQL authoritative/canonical truth'tür. LanceDB vector ve Kùzu graph
projection'ları rebuildable derived state'tir ve yalnız exclusive writer-lock
boundary altında kopyalanır.

Restore mevcut hedefi ezmemelidir. Backup'ı yeni boş root'a, uyumlu kod ve
config ile geri yükleyip doğrulayın. V3 verisini V4'e taşımak ayrı kaynak/catalog
eşleme ve V4 ingestion çalışmasıdır; projection runner V3→V4 dönüştürücüsü
değildir. [Migration rehberi](RUNBOOK.md#upgrade-v080) V4 yükseltmesini açıklar.
Backup rollback süresi boyunca korunur.

V4 projection rebuild'inin submit → drain → stop → offline
run/resume → restart → status → automatic rollback akışı, exit code'ları ve
retained generation kuralları
[`v4-rebuild-runbook.md`](v4-rebuild-runbook.md) içinde kanonik olarak
belgelenir. Dataset/tenant-scoped rebuild veya online runner desteklenmez.

Alembic migration'ları forward-only'dir. Bir migration başarısız olursa
`alembic downgrade` çalıştırmayın: release'i durdurun, doğrulanmış backup'ı
yeni boş bir storage root'a restore edin, parity kontrolünü yeniden çalıştırın
ve ancak ardından cutover yapın.

### Single-writer kapasite sınırı

V4 bir storage root için yalnız bir aktif `combined` owner kullanır.
Worker başlangıcı aynı hostta advisory lock ile ikinci writer'ı reddeder;
çoklu host/NFS için bu bir koordinasyon mekanizması değildir. API, valence ve
MCP process-local cache'leri yalnız performans optimizasyonudur; doğruluk
durumu SQLite ledger ve fenced lease'lerdedir. Yatay writer ölçekleme ancak
paylaşımlı lease/koordinasyon servisi tasarlanıp doğrulandıktan sonra açılır.

## 7. Model/provider olayı

`MESA_MODEL_ENABLED` ve `MESA_EXTERNAL_PROVIDER_ENABLED` açıkça ayarlanır.
Provider timeout/rate-limit hatası retryable; bozuk şema permanent; sürekli
aynı toksik payload poison olarak sınıflanır. Seçili validation policy
tamamlanmadan SQL/vector/graph projection başlamaz. Provider’ı atlamak için mutation’ı doğrudan VALIDATED
duruma çekmeyin.

## 8. PageRank

V4 PageRank çıktısı yalnız telemetridir. Düşük merkezilik bir memory,
assertion, vector veya source’u karantinaya alamaz. PageRank temelli
`hallucination`/delete davranışı görülürse deploy durdurulmalı; bu v4
sözleşmesine aykırıdır.

## 9. Session finalization

Session end durable finalization kaydı oluşturur. Restart sonrası pending işler
combined consumer tarafından alınır. Uzun süre pending kalırsa session,
finalization ID, lease ve DLQ durumu kontrol edilir; FastAPI process içi
`BackgroundTasks` çalışmasına güvenilmez.

## 10. Logging ve olay kanıtı

Production varsayılanı `MESA_LOG_LEVEL=INFO` ve `MESA_LOG_FORMAT=json`’dır.
Loglarda request/operation/mutation/pipeline run kimlikleri bulunabilir;
query, source content, payload, credential, raw model output ve claim token
bulunamaz. Docker `local` driver 10 MB × 5 dosya sınırı kullanır.

Incident kanıtı olarak maskelenmiş log, mutation status, health/metrics anlık
görüntüsü, image digest ve storage manifesti saklanır.

## 12. Release durumu

Unit/integration/contract testlerinin geçmesi production GO değildir. Gerçek
provider ve production-benzeri store ile backup→restore→rebuild→cutover,
queue saturation, worker crash, cross-tenant concurrency ve 24 saat soak
kanıtları tamamlanana kadar karar `NO-GO` kalır.

<a id="troubleshooting"></a>

## Troubleshooting

Capture version/commit, runtime profile, content-free health, mutation/operation
IDs and error class. Never include keys, claim tokens or full source/query text.

| Symptom | Check | Safe response |
|---|---|---|
| Missing runtime profile/root | Explicit environment and CLI help | Use a dedicated writable root, never repo/home/filesystem root |
| 401 | Key registry, active principal, key rotation | Re-provision/reload authorized credentials; do not disable auth |
| 403/session conflict | ACL, session principal and dataset/agent binding | Grant intended scope offline; start a properly scoped session |
| Insert 422 | Required title/IDs, character bounds and content | Compare actual OpenAPI schema; keep exact source |
| Insert 409 | Frozen revision, identity/idempotency conflict | Reuse exact retry payload or create a new revision |
| Embedding 409/mismatch | Full provider/model/revision/dimension/normalization identity | Follow offline migration/rebuild; equal dimension is not equal space |
| `503 vector_backend_unavailable` | Provider availability and operational identity | Restore the selected provider; no silent model switch |
| `503 graph_backend_unavailable` | Health, timeout, Kùzu schema/parity | Diagnose query/backend and bounded traversal; do not advertise complete results |
| `503 maintenance_pending` | Active/retryable rebuild status | Follow [rebuild runbook](v4-rebuild-runbook.md); no manual lease/queue deletion |
| Mutation stalls or enters DLQ | Projection predecessor, fenced claim, provider errors | Repair cause, then authorized replay if state permits |
| Semantic rejection | Rejection reason and validation policy | Treat as terminal; generic replay returns `NON_REPLAYABLE` |
| Cleanup `BLOCKED` | Store availability and shared ownership | Preserve evidence, repair store, run supported recovery |
| Readiness 503 | Worker liveness, thresholds, canonical backlog | Resolve health; process alive does not mean ready |
| FTS/parity error | Matching Alembic head and checksummed backup | Stop writers and rehearse recovery into a new root |
| Old Kùzu timestamp/schema error | Release code and offline schema migration | Upgrade/rehearse from backups; do not issue ad hoc ALTER or wipe |
| Growing RSS/disk | Lance fragments/versions, queues and retained generations | Measure representative workload; apply supported maintenance, reserve capacity |

Inspect `/v4/capability` before assuming semantic/graph availability. A model-disabled
profile intentionally lacks semantic generation. `wait_until_committed()` can
return failed terminal states; inspect `state` explicitly.

CLI `--help`, [configuration](configuration.md), [API](api-reference.md),
[operations](RUNBOOK.md) and [migration](RUNBOOK.md#upgrade-v080) describe supported
interfaces. Avoid raw SQL edits, in-place restore, forced queue completion and
unbounded retries. Real provider or production-size evidence unavailable in an
audit must remain `NOT EXECUTED`, never converted to a simulated success.


<a id="upgrade-v080"></a>

## Upgrading to v0.8.0

Current package metadata is 0.8.1. Publication is owner-approved with the limitations in
the [release notes](../CHANGELOG.md#v081-publication); it does not certify production
readiness. Confirm actual tags in GitHub Releases. This guide describes changes
since v0.7.1, not a completed production migration.

v0.8.1 retains the v0.8.0 schema head below. It adds storage/retrieval hardening
and developer-experience fixes; it introduces no new schema migration. The
existing embedding identity, offline upgrade and backup requirements still apply.

### Preconditions and changed contracts

Use a stopped, backed-up storage root and rehearse on a new disposable root.
Record source commit, lockfile, provider/model/revision/dimension/normalization,
Alembic head, artifacts and rollback path. RBAC has its own schema version.

Since v0.7.1, the tree includes immutable revision manifest activation, opaque
physical catalog identity, declared/manifest/chunk hash separation, durable
rebuild operations/generations, provider identity, assertion FTS/representation
and typed legal identity migrations. Current source expects Alembic head
`d5e6f7a8b9c0`. Do not assume an old database is compatible just because the
package version stayed 0.7.1 throughout the development branch.

New insertion/reprojection uses `assertion-semantic-v1`; migrated old vectors
are `legacy-v0` and cannot contribute as current semantic evidence. Reindex via
the supported full offline [rebuild](v4-rebuild-runbook.md). Unknown legacy provider
identity can be adopted only with external proof and a fresh unclaimed pending
operation. Matching dimensions alone do not prove matching embedding spaces.

Search/context carry temporal and jurisdiction filters; graph ON/OFF ablation
and bounded diagnostics are available. Context has atomic proof packing and an
opt-in adaptive mode. HTTP insert supports typed legal identity, while SDK
insert does not currently expose it. Invalid/frozen/idempotency-conflicting
source identity fails rather than silently overwriting. Semantic `REJECTED`
replay returns `409 NON_REPLAYABLE`.

### Migration configuration

`mesa_storage/alembic.ini` owns the migration database URL.
`MESA_STORAGE_ROOT` alone does not change that URL. With all writers stopped,
prepare a reviewed copy of the Alembic configuration with an absolute staging
SQLite URL and absolute `script_location` pointing to this release's migrations:

```bash
.venv/bin/alembic -c /srv/mesa/reviewed-staging-alembic.ini upgrade head
```

The path is an operator example, not a configuration supplied by the repository.
Use a verified restored staging root and the matching release environment.
This migrates canonical V4 schema; the [projection runner](v4-rebuild-runbook.md)
rebuilds its derived stores. Neither procedure is a general V3 content converter.
V3 content needs explicit source/catalog mapping and V4 ingestion into a separate root.

### Upgrade and rollback sequence

1. Drain work, stop writers and take a checksummed backup under the writer-lock boundary.
2. Validate the backup and restore to a new empty staging root.
3. Apply forward migrations offline with the supported release environment.
4. Rebuild derived projections where required; check scope, provenance and parity.
5. Rehearse start, insert, poll, search, restart and negative authorization.
6. Cut over only with full release evidence and retain previous roots/generations.

Alembic downgrade is not the rollback strategy. Restore a verified prior backup
into a new empty root using the matching prior code/config, then validate before
switching traffic. V3 and V4 never share writable physical storage. Do not alter
official certification evidence as part of upgrade verification.
