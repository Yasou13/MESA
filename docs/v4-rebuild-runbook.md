# V4 projection rebuild runbook

Bu prosedür yalnız `0.7.x Closure` içindeki storage-root-wide projection
rebuild içindir. Canonical `mesa.db` ve FTS yeniden üretilmez veya değiştirilmez;
LanceDB ve Kùzu projection generation'ları canonical snapshot'tan yeniden
oluşturulur. Tenant, workspace veya dataset kapsamlı rebuild desteklenmez.

## Güvenlik sınırları ve ön koşullar

- `MESA_V4_REBUILD_ENABLED` varsayılan olarak `false` kalır. Yalnız onaylı
  rehearsal/değişiklik penceresinde API ve runner için `true` yapın.
- API işlemleri principal'a bağlı credential ve control-plane `ADMIN` rolü
  ister. Dataset rolleri tek başına yeterli değildir.
- Rebuild online değildir. Submit yazma admission'ını kapatır; backlog drain
  edildikten sonra combined runtime durdurulur ve runner storage writer
  lock'ını tek başına alır.
- Trusted, storage ve work root'ları önceden var olmalı; storage/work trusted
  root'un gerçek alt dizinleri olmalı, birbirleriyle örtüşmemeli ve symlink
  içermemelidir. Bu path'ler HTTP request'iyle alınmaz.
- Work filesystem'de en az storage boyutunun iki katı ve runner'ın güvenlik
  rezervi kadar boş alan bulunmalıdır. Runner kendi ölçümünü yapar ve yetersiz
  alanda fail-closed davranır.
- API ile runner aynı provider, embedding model/version ve dimension config'ini
  kullanmalıdır. `MESA_EMBEDDING_VERSION` boş bırakılamaz; dimension gerçek
  provider çıktısıyla aynı olmalıdır. Snapshot provenance'ı uyuşmuyorsa runner
  rebuild yapmaz.
- Backup ve retained generation otomatik silinmez. Bu release'te otomatik veya
  zamanlanmış projection-generation cleanup yoktur.

Başlamadan önce gerçek-provider rehearsal, production-benzeri disk/concurrency
kanıtı ve geri dönüş penceresi için yeterli kapasite doğrulanmalıdır.

## 1. Operator yetkisini ve flag'i hazırla

Flag'i combined runtime'ın açık deployment config'inde etkinleştirin:

```bash
export MESA_V4_REBUILD_ENABLED=true
export MESA_EMBEDDING_VERSION=v1
export MESA_EMBEDDING_DIMENSION=1536
```

Credential'ı bir kez üretin ve aynı principal'a control-plane rolü verin.
Credential'ı shell history, log veya runbook kaydına kopyalamayın:

```bash
export MESA_STORAGE_ROOT=/srv/mesa/v4-data
mesa-v4-admin issue-key --principal rebuild-operator
mesa-v4-admin grant-control --principal rebuild-operator
```

Runtime yeniden başladıktan sonra capability gerçeğini kontrol edin:

```bash
curl --fail \
  -H "X-API-Key: $MESA_API_KEY" \
  http://127.0.0.1:8000/v4/capability
```

`capabilities.durable_rebuild` değeri `true`, `limits.rebuild_scope` değeri
`storage_root` ve `limits.requires_offline_runner` değeri `true` olmalıdır.

## 2. Durable operation submit et

Her değişiklik penceresi için benzersiz, içerik taşımayan bir idempotency key
kullanın:

```bash
export MESA_REBUILD_IDEMPOTENCY_KEY=rehearsal-2026-08-03-a
curl --fail-with-body -X POST \
  -H "X-API-Key: $MESA_API_KEY" \
  -H "Idempotency-Key: $MESA_REBUILD_IDEMPOTENCY_KEY" \
  http://127.0.0.1:8000/v4/operations/rebuild
```

Başarılı durable commit `202` ve `PENDING` operation döndürür. Aynı key ve aynı
sabit storage-root payload aynı operation'ı döndürür. Çakışan payload veya aynı
root'ta başka aktif rebuild `409` olur. `Idempotency-Key` zorunludur.

Yanıttaki `operation_id` değerini secret olmayan incident/change kaydına alın:

```bash
export MESA_REBUILD_OPERATION_ID=00000000-0000-4000-8000-000000000000
```

Submit sonrası v4 mutation endpoint'leri `503 maintenance_pending` ve
`Retry-After: 5` döndürür. Read, capability, health, metrics ve operation status
açık kalır. `RETRYABLE_FAILED` da maintenance admission'ını kapalı tutar.

## 3. Backlog'u drain et

Combined runtime'ı henüz durdurmadan `/health`, `/health/init` ve `/metrics`
üzerinden şu değerlerin sıfıra indiğini doğrulayın:

- projection backlog ve dead letter;
- cleanup backlog ve blocked cleanup;
- stuck/in-flight projection lease;
- dispatch/raw-log işleri, vector WAL ve session finalization işleri.

Özellikle `mesa_v4_projection_backlog`,
`mesa_v4_projection_dead_letter`, `mesa_v4_projection_stuck_leases`,
`mesa_v4_cleanup_backlog` ve `mesa_v4_cleanup_blocked` sıfır olmalıdır. Runner
canonical SQLite'taki tüm kuyrukları ve storage-root DLQ dosyasını tekrar
kontrol eder; sıfır olmayan herhangi bir değer rebuild'i reddeder. Kuyruk
kaydını veya lease'i elle silerek preflight'ı atlamayın.

## 4. Combined runtime'ı durdur ve offline runner'ı çalıştır

Backlog sıfırlandıktan sonra combined process'i kontrollü durdurun. Process'in
storage writer lock'ını bıraktığını doğrulayın. Sonra aynı reviewed provider
config'iyle şu komutu çalıştırın:

`fd4e5f6a7b8c` öncesinde oluşturulmuş aktif vector mutation'larında provider
kimliği eksikse normal runner fail-closed olur. Adoption yalnız henüz hiç claim
edilmemiş (`attempt_count=0`), `source_manifest_hash` yazılmamış fresh `PENDING`
operation üzerinde yapılabilir. Combined runtime durmuş ve
provider/model/version/dimension eski generation'ı üreten config kayıtlarından
dışarıdan doğrulanmışsa bir kez şu açık adoption adımını çalıştırın:

```bash
mesa-v4-rebuild adopt-provider \
  --trusted-root /srv/mesa \
  --storage-root /srv/mesa/v4-data \
  --provider openai_compatible \
  --model text-embedding-3-small \
  --version "$MESA_EMBEDDING_VERSION" \
  --dimension "$MESA_EMBEDDING_DIMENSION" \
  --confirm-legacy-provider-unknown
```

Bu komut rebuild değildir: yalnız aktif vector ownership'ine bağlı canonical
mutation satırlarındaki eksik provider alanlarını `COALESCE` ile tamamlayan,
writer-lock korumalı ve açık operator onayı isteyen tek seferlik metadata
adoption işlemidir. Var olan herhangi bir provider/model/version/dimension
değeri assertion ile çelişirse transaction bütünüyle geri alınır. Bu kanıt
yoksa adoption yapmayın; release `NO-GO` kalır ve sonraki full raw-source
rebuild iş paketini bekleyin. `mesa-v4-rebuild run` canonical SQLite'ı hiçbir
zaman değiştirmez.

Bir rebuild denemesi başladıysa adoption canonical manifest'i değiştirebileceği
için aynı backup/checkpoint ile resume güvenli değildir ve komut fail-closed
olur. Bu kural `source_manifest_hash` henüz yazılmadan oluşan crash penceresini
de kapsar; hiçbir `RETRYABLE_FAILED` operation üzerinde adoption yapılmaz. Eski
operation'ı cancel edin, yeni bir `Idempotency-Key` ile fresh rebuild submit
edin, backlog'u yeniden drain edip runtime'ı durdurun ve adoption'ı yeni
`PENDING` operation üzerinde runner'dan önce çalıştırın. Eski operation'ın
backup veya checkpoint'ini yeni operation'da kullanmayın.

Ardından aynı reviewed provider config'iyle normal runner'ı çalıştırın:

```bash
mesa-v4-rebuild run \
  --trusted-root /srv/mesa \
  --storage-root /srv/mesa/v4-data \
  --work-root /srv/mesa/rebuild-work \
  --operation-id "$MESA_REBUILD_OPERATION_ID"
```

İsteğe bağlı `--batch-size` sınırı `1..1000`, `--lease-seconds` sınırı
`30..3600` arasındadır. Runner sırasıyla writer lock, Alembic head, fenced
operation lease, backlog, path/symlink, disk, checksummed backup, source
manifest, provider provenance, deterministic vector/graph replay, parity ve
cutover kontrollerini yürütür.

Exit code'lar:

| Code | Anlam | Operator işlemi |
|---|---|---|
| `0` | `COMPLETED` | Runtime'ı yeniden başlatma adımına geçin |
| `2` | Flag/config/operation reddi | Config ve operation status'unu düzeltin; kör retry yapmayın |
| `3` | Güvenli retryable failure | Status/checkpoint'i inceleyip aşağıdaki resume akışını kullanın |
| `4` | Başka writer aktif | API/worker/combined process'i bulun; ikinci writer başlatmayın |
| `5` | Retry bütçesi tükendi, `FINAL_FAILED` | Rehearsal'ı durdurun; yeni operation öncesi kök nedeni giderin |

Runner stdout/stderr çıktısı operation/generation/state/count/error-class ile
sınırlıdır. Fiziksel path, içerik, credential, manifest payload veya claim
token loglanmamalıdır.

## 5. Resume, retry ve cancel

Kontrollü hata `RETRYABLE_FAILED` üretmişse aynı durable operation ve checkpoint
üzerinde önce retry isteği gönderin, sonra aynı runner komutunu çalıştırın:

```bash
curl --fail-with-body -X POST \
  -H "X-API-Key: $MESA_API_KEY" \
  "http://127.0.0.1:8000/v4/operations/$MESA_REBUILD_OPERATION_ID/retry"
```

API bunun için geçici olarak yeniden başlatılmışsa runner'dan önce tekrar
durdurulmalıdır. `retry` yalnız `RETRYABLE_FAILED` durumunda `202` döndürür ve
checkpoint'i silmez. Retry budget tükenirse `409` döner.

Process `SIGKILL`, host crash veya güç kaybıyla aktif durumda kesildiyse API
retry çağrısı yapmayın. Lease süresi dolduktan sonra aynı runner komutu stale
owner'ı yeni fencing token ile reclaim eder. Tamamlanmış batch'ler yalnız source
manifest değişmemişse kullanılır. Crash `READY_TO_CUTOVER` sırasında olduysa
runner pointer değişmemiş veya target'a değişmiş iki güvenli durumu ayırır;
replay yapmadan yeniden doğrular, tamamlar ya da retained source'a döner.

Operasyondan vazgeçmek yalnız `PENDING` veya `RETRYABLE_FAILED` için güvenlidir:

```bash
curl --fail-with-body -X POST \
  -H "X-API-Key: $MESA_API_KEY" \
  "http://127.0.0.1:8000/v4/operations/$MESA_REBUILD_OPERATION_ID/cancel"
```

`CANCELLED` eski active generation ile mutation admission'ını yeniden açar.
Staging generation ve backup otomatik silinmez. `CLAIMED`, `RUNNING`,
`VERIFYING` veya `READY_TO_CUTOVER` operation'ını cancel etmeye çalışmak `409`
olur; runner'ı önce güvenli batch sınırında durdurun.

## 6. Cutover, otomatik rollback ve restart

Pre-cutover parity başarısızsa pointer değiştirilmez. Parity geçerse vector ve
graph birlikte tek SQLite generation pointer transaction'ıyla aktive edilir.
Post-cutover reopen/health/smoke başarısızsa runner pointer'ı retained source
generation'a otomatik döndürür, rollback'i tekrar doğrular ve operation'ı
`RETRYABLE_FAILED` yapar.

Bu release tamamlanmış operation için manuel HTTP rollback endpoint'i sunmaz.
`COMPLETED` sonrası yeni generation'da incident görülürse tüm writer'ları
durdurun; pointer tablolarını veya generation dizinlerini elle değiştirmeyin.
Backup/retained generation'ı koruyun ve recovery prosedürüyle incident
escalation yapın. Geç activation crash'i aynı operation'ın reclaim akışıyla
otomatik olarak tamamlanır veya geri alınır.

Runner `0` döndürdükten ya da operation güvenle cancel edildikten sonra combined
runtime'ı yeniden başlatın. Status'u control-plane `ADMIN` credential ile
doğrulayın:

```bash
curl --fail \
  -H "X-API-Key: $MESA_API_KEY" \
  "http://127.0.0.1:8000/v4/operations/$MESA_REBUILD_OPERATION_ID"
```

Yetkisiz status/cancel/retry çağrıları operation varlığını sızdırmamak için
`404` döndürür. Status yalnız kind/scope/state/attempt, progress, bounded
error-class ve zaman alanlarını içerir.

Tamamlanmış değişiklik penceresinden sonra yeni submit/retry gerekmiyorsa flag'i
tekrar `false` yapıp reviewed config ile runtime'ı yeniden başlatın. Existing
operation status ve health flag kapalıyken de okunabilir.

## 7. Gözlemlenebilirlik ve retained artifact'lar

İzlenecek rebuild metrikleri:

- `mesa_v4_rebuild_operation_state{state=...}`;
- `mesa_v4_rebuild_duration_seconds`;
- `mesa_v4_rebuild_progress_completed` ve
  `mesa_v4_rebuild_progress_total`;
- `mesa_v4_rebuild_parity_missing`;
- `mesa_v4_rebuild_staging_bytes`;
- `mesa_v4_rebuild_rollbacks_total`.

`/health` rebuild bölümünde operation ID veya filesystem path olmadan health,
state, duration, progress ve rollback sayısını verir. `FINAL_FAILED` degraded;
aktif ve `RETRYABLE_FAILED` durumları maintenance kabul edilir.

Backup, failed staging generation ve retained generation için ayrı change
record tutun. Bu release'te hiçbirini otomatik silmeyin. Silme/retention
politikası sonraki açık operator iş paketidir.

## 8. API/SDK sınırı ve production kararı

Deprecated `POST /v4/rebuild` yalnız storage-root alias'ıdır. `tenant_id`,
`workspace_id` veya `dataset_id` verilirse `409` döner. Dataset-bound MCP
araçları global rebuild submit/cancel/retry sunmaz. Sync ve async Python
SDK'lar `capability`, `submit_rebuild`, `operation_status`, `cancel_operation`
ve `retry_operation` metotlarını sunar.

Merge, unit/integration test veya CI evidence tek başına production `GO`
değildir. Aşağıdakilerin tamamı aynı release adayıyla tamamlanana kadar karar
`NO-GO` kalır:

- gerçek LanceDB/Kùzu ve seçilen embedding provider ile backup → rebuild →
  reopen → parity → rollback rehearsal;
- production-benzeri disk kapasitesi, dataset büyüklüğü ve concurrency;
- migration/DR ve content-free closure evidence artifact'ları;
- cross-tenant/dataset negatif retrieval ve crash/fencing kanıtı;
- kesintisiz 24 saat soak ve operasyon onayı.
