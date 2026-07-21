# Remediation Policy

## Kanıt seviyeleri

| Seviye | Tanım |
|---|---|
| E0 | İddia / kanıtsız |
| E1 | Static code/config |
| E2 | Unit/component |
| E3 | Isolated integration/runtime |
| E4 | Staging/rehearsal |

## Canonical durumlar

`Open`, `Confirmed open`, `In remediation`, `Partially fixed`, `Fixed but not verified`, `Verified resolved`, `Mitigated`, `Deferred`, `Blocked`, `False positive`, `Duplicate`, `Superseded`, `Not tested`, `Not verified`.

## Finding kapatma kuralı

Bir finding yalnız şu eksiksiz zincirle `Verified resolved` olabilir:

`Confirmed open` → deterministic failure/reproduction → minimal fix → target test pass → related regression pass → cross-system impact check → gerekli minimum E2/E3/E4 kanıtı → canonical audit reconciliation.

Statik kod değişikliği tek başına yeterli değildir.

## Wave sınırları

Varsayılan bir wave en fazla 1 kök neden, 3 canonical finding, 5 kaynak dosyası, 3 yeni test dosyası, 1 migration/schema kararı ve 1 public contract değişikliği içerir.

## Sequential-auto with controlled recovery

Runner wave'leri yalnız `QUEUE.md` dependency sırasıyla çalıştırır; aynı anda yalnız bir wave `Running` olabilir. Bir wave evidence, cross-system kontrol, canonical reconciliation ve checkpoint tamamlanmadan sonraki wave başlatılmaz. `VERIFIED_COMPLETE` doğrudan dependency açabilir. `FIXED_NOT_VERIFIED` yalnız eksik kanıt bağımsız bir sonraki verification wave'inde üretilebilecekse ve sonraki iş buna bağlı değilse queue ilerlemesini engellemez.

Regresyon önce R1/R2/uygun R3 olarak deterministic biçimde doğrulanır, en fazla wave başına üç kontrollü düzeltme ve issue başına iki patch denemesiyle minimal olarak giderilir. R4, çözülemeyen regresyon veya rollback belirsizliğinde runner durur.

## Durma koşulları

`STOP_BLOCKED`: gerçek secret veya production verisi gereksinimi, kullanıcı değişikliğiyle conflict, destructive migration, rollback yolunun olmaması, yeni cross-tenant veya veri kaybı riski, güvenli test izolasyonunun olmaması, RAM/disk sınırı ya da Docker/servis/donanım eksikliği.

`STOP_FOR_DECISION`: identity model kararı, public API breaking change, kalıcı schema değişikliği, yeni dependency/servis, backward compatibility ile security çatışması veya migration stratejisi seçimi.

## Donanım ve servis sınırları

16 GB RAM, Intel Iris; CUDA/ROCm yoktur. Ollama otomatik yönetilmez; model indirilmez, silinmez veya güncellenmez. Gerçek provider, ağır load/stress/soak, `pytest-xdist`, sınırsız thread/worker, Docker prune ve production secret okuması yasaktır.

## GO kuralları

Kurulum aşamasında GO hesaplanmaz. Gelecekte GO ancak açık P0=0, açık release-blocking P1=0, security/data/worker E3, migration/restore doğrulaması, artifact/startup/restart, staging rehearsal, rollback ve clean pinned commit/artifact ile mümkün olabilir.

## Sequential continuation reconciliation

`VERIFIED_COMPLETE` bütün açık dependency’leri açabilir. `FIXED_NOT_VERIFIED` finding’i kapatmaz ve final security/data/release gate’ini geçemez; ancak eksik kanıta teknik olarak bağımlı olmayan wave’leri bloke etmez. `PARTIALLY_COMPLETE`, `BLOCKED`, `STOPPED_FOR_DECISION`, `ROLLED_BACK` ve `FAILED_SAFE` varsayılan olarak runner’ı durdurur. Queue değerlendirmesi wave sonucuna değil, açıkça tanımlı teknik dependency’ye dayanır.
