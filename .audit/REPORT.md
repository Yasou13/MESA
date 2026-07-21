# Production Readiness Report

* **Genel karar**: NO_GO
* **Açık release blocker'lar**: External dependencies (Docker, CI) ve 35 açık/doğrulanmamış kritik bulgu
* **Doğrulanmış düzeltmeler**: 12
* **Doğrulanmamış düzeltmeler**: 26 (FIXED_UNVERIFIED)
* **Kalan riskler**: Docker daemon, remote CI, production-like deployed consumer topology doğrulanamaması
* **Gerekli sonraki işlemler**: FNV (Fixed Not Verified) bulgularının uygun ortamda (CI/Docker) regression/integration testleriyle doğrulanması
* **Kararın dayandığı test ve kanıtlar**: Mevcut test logları ve Faz 9 E2/E3 test kapsam eksiklikleri (Bkz. `evidence/` klasörleri)

## 2026-07-21 — SEC-003 remediation checkpoint

Rate-limit subject'i raw credential veya request agent/tenant alanından değil,
doğrulanmış server-side `principal_id` üzerinden çözülür. Yeni additive Alembic
revision `c3d4e5f6a7b8`, eski `daily_limits.agent_id` satırlarını kopyalamadan
tabloyu `subject_id` ile yeniden oluşturur; bu bilinçli bir one-time counter
reset'tir. Sentetik credential için disposable SQLite DB ve backup dump negatif
kanıtı alınmıştır. İzole deployed API/runtime, gerçek backup/log pipeline ve
clean CI kanıtı yoktur; SEC-003 `FIXED_UNVERIFIED`, release kararı `NO_GO`
olarak kalır.

## 2026-07-21 — OPS-001 external remediation checkpoint

Hash'li `pip-tools` lock üretimi için gerekli resolver/cache yerelde yoktur ve
bu makine için tanımlanan clean-install/download sınırı dışındadır. Mevcut
`pyproject.toml` version range'leri, editable CI kurulumları ve Docker'ın
dependency çözümü deterministik değildir. Geçersiz/elde yazılmış lock veya
çalışmayacağı bilinen `--require-hashes` CI değişikliği yapılmadı. OPS-001,
izole clean CPU runner'da lock üretimi, hash install, wheel/install smoke ve
drift-gate kanıtına kadar `OPEN_EXTERNAL_BLOCKED` kalır.

## 2026-07-21 — MIG-002/003 coordinator checkpoint

Offline SQLite-edge→Kùzu bulk migration için Linux `flock`, SQLite journal,
fencing token, versioned staging artifact, postflight callback ve atomik
promote/retained rollback coordinator'ı eklendi. Yeni CLI canlı hedefte
`--wipe` kabul etmez. Runtime `kuzu_setup` mevcut journal'sız graph üzerinde
ALTER çalıştırmak yerine fail-closed durur; `migrate_kuzu_schema.py` graph'ı
staging'e kopyalar, checksum doğrular ve promote eder. Seven dar test içinde
gerçek Kùzu staging/swap ve unjournaled startup rejection kanıtı geçti.
Kùzu'nun bu ortamda dosya biçiminde depolanabildiği gözlendi; coordinator dosya
veya dizin artifact'lerini aynı filesystem üzerinde swap eder. Deployed
restart/swap kanıtı bulunmadığı için MIG-002/003 `FIXED_UNVERIFIED` ve genel
karar `NO_GO` kalır.

### 2026-07-21 — Kullanıcı onaylı yerel Kùzu artifact geçişi

`storage/kuzu_db` için offline şema CLI çalıştırıldı. Geçiş journal'da
`PROMOTED`, hedef sürüm `2` ve fencing token `1` olarak kaydedildi. Salt-okunur
sonraki kontrolde `Entity=2` ve `Observed=0` değerleri korundu; mevcut runtime
başlatma yolu da hazır döndü. Önceki artifact, transaction sonrası retained
rollback yolu olarak saklandı. Bu yalnız yerel artifact kanıtıdır; dağıtık
deployment restart/swap ve legacy bulk import E3 kanıtının yerini tutmaz.

## 2026-07-21 — WORKER-001 remediation checkpoint

Kök neden, ayrı `worker-only` rolünün heartbeat üretmesine rağmen `api-only`
profilinin bunu Compose readiness kararına katmamasıydı. Yeni
`MESA_REQUIRE_WORKER_READINESS` bayrağı varsayılan olarak kapalıdır; bu nedenle
bilerek workerless çalışan bağımsız API davranışı değişmez. Compose API rolü bu
bayrağı açar ve `/health/init`, shared storage altında taze worker heartbeat
yoksa `503` döner. Negatif/pozitif API readiness, profile ve deployment
sözleşmelerini kapsayan 17 dar test geçti.

Yerel gerçek API+worker süreç çifti denemesinin ön koşulu, disposable SQLite
üzerinde hem `AsyncEngine.initialize()` hem doğrudan `aiosqlite.connect()`
aşamasında zaman aşımına uğradı; bu uygulama worker koduna ulaşmadan oluştu.
Bu nedenle WORKER-001 `FIXED_UNVERIFIED` kalır. Docker erişimli CPU runner'da
worker stop/restart sırasında Compose API `/health/init` geçişinin doğrulanması
E3 kapanış koşuludur.

## 2026-07-21 — DLQ-001 revalidation checkpoint

Mevcut kod, audit'teki eski `OPEN` kaydının aksine DLQ item'larını process
dosya kilidi altında lease ve claim token ile alır. Ack/nack sahiplik ile
fenced'dir; tamamlanmış yan etki önce fsync edilmiş receipt ile kaydedilir ve
restart sonrası receipt reconcile edilir. Legacy veya eksik `agent_id`/`cmb_id`
item'ları silinmez, nack edilir.

Disposable JSONL üzerinde aynı synchronous claim/ack yolu tek owner, yabancı
ack reddi ve owner ack sonrası boş kuyruk sonucunu verdi. Async DLQ contract
testi ise executor iş parçacığına geçişte yerel timeout'a girdi; bu nedenle
`DLQ-001` yalnız `FIXED_UNVERIFIED` olarak güncellendi. Executor concurrency,
crash sonrası receipt reconciliation ve deployed worker restart E3 kanıtı
gereklidir.

## 2026-07-21 — DATA-002 remediation checkpoint

`MemoryDAO.insert_memory` ve `bulk_insert_memory`, graph provider hatasını
yutmaz. Vector projection telafi edilir ve SQLite canonical transaction'ı
başlatılmadan aynı hata çağırana geri verilir. Yeni bulk regression, ikinci
graph node yazımının hatasında tüm batch vector'larının soft-delete edildiğini
ve SQLite transaction sayısının sıfır kaldığını doğruladı; ilgili dar suite
toplam dört test geçti.

Gerçek Kùzu içinde bulk çağrının ilk node'u oluşturup sonraki node'da çökmesi
gibi crash/reconciliation senaryosu yerel ortamda doğrulanmadı. Bu nedenle
DATA-002 `FIXED_UNVERIFIED` kalır; external runner'da graph/vector/SQLite
reconciliation E3 kapanış koşuludur.

## 2026-07-21 — DATA-001 revalidation checkpoint

Purge kodu artık yalnız SQLite tombstone ile bitmez. Journal exact target node
listesini ve idempotency anahtarını kaydeder; downstream sıra Kùzu
`delete_nodes`+`verify_nodes_absent`, ardından vector `hard_delete`+aktif-id
doğrulamasıdır. Her downstream hatası journal'ı `RETRY_PENDING`/gerekirse
`BLOCKED` tutar; `resume_purge` ve `resume_incomplete_purges` aynı dar kapsamı
yeniden yürütür. Başarılı son durum `FINALIZED` olur.

Yedi senaryolu disposable SQLite suite bu yerel oturumda ilk `aiosqlite`
bağlantısında zaman aşımına uğradı; dolayısıyla yeni E2 sonucu üretilemedi.
Kod ve sözleşme mevcut olsa da real SQLite/Kùzu failure-retry-recovery ve
restore E3 kanıtına kadar DATA-001 `FIXED_UNVERIFIED` kalır.
