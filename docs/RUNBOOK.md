# MESA operations runbook

Bu runbook yayınlanmamış v4 full-cognitive runtime için kanonik operasyon
rehberidir. V3 lexical-core uyumluluk işlemleri ayrıca belirtilir.

## 1. Desteklenen v4 topology

V4 aynı storage root’a yazan tek bir `combined` process ile çalışır. API
admission, durable consumer, validation ve SQLite/LanceDB/Kùzu projection
işleri bu process’tedir.

```bash
export MESA_RUNTIME_PROFILE=combined
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
export MESA_LOAD_DOTENV=false
python -m mesa_memory.runtime_entrypoint
```

Aynı storage root’u ikinci API, worker veya combined process’e yazılabilir
olarak mount etmeyin. Yatay ölçekleme öncesi storage ownership protokolü
gerekir. V3 ile v4 farklı fiziksel storage root kullanmalıdır.

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

Önce `/health`, `/health/init` ve `/metrics` kontrol edilir. İzlenecek temel
metrikler:

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

Restore mevcut hedefi ezmemelidir. V3 migration yerinde yapılmaz: manifestli
backup alınır, ayrı v4 root’a offline rebuild yapılır, parity raporu geçerse
atomik cutover gerçekleştirilir. Backup rollback süresi boyunca korunur.

## 7. Model/provider olayı

`MESA_MODEL_ENABLED` ve `MESA_EXTERNAL_PROVIDER_ENABLED` açıkça ayarlanır.
Provider timeout/rate-limit hatası retryable; bozuk şema permanent; sürekli
aynı toksik payload poison olarak sınıflanır. Tier-3 geçmeden SQL/vector/graph
projection başlamaz. Provider’ı atlamak için mutation’ı doğrudan VALIDATED
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

## 11. V3 compatibility

`docker-compose.yml` ayrı API + worker lexical-core topology’sidir. V3’teki
telafi edici Saga, agent/session RBAC, soft-delete ve maintenance davranışı
v4’e genellenmez. V3 sorunlarında mevcut `mesa-recovery`, worker readiness ve
maintenance prosedürleri uygulanır; aynı storage root v4’e bağlanmaz.

## 12. Release durumu

Unit/integration/contract testlerinin geçmesi production GO değildir. Gerçek
provider ve production-benzeri store ile backup→restore→rebuild→cutover,
queue saturation, worker crash, cross-tenant concurrency ve 24 saat soak
kanıtları tamamlanana kadar karar `NO-GO` kalır.
