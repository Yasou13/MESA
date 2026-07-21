# WAVE-006 — MIG-001 Alembic drift/adoption sözleşmesi

Tarih: `2026-07-21`  
Branch: `remediation/production-blockers`  
Başlangıç HEAD: `731d35b`  
Durum: `FIXED_NOT_VERIFIED`

## Kapsam ve kök neden

`MIG-001` için ilk Alembic migration'ın `CREATE ... IF NOT EXISTS` kullanması,
pre-Alembic bir SQLite dosyasının gerçek yapısı doğrulanmadan revision durumuna
ilerletilebilmesine izin veriyordu. Eksik `nodes.content_payload` gibi temel bir
sözleşme farkı, yanlış biçimde yönetilen şema olarak görülebiliyordu.

## Uygulanan minimum remediation

- `mesa_storage/schema_contract.py`: SQLite `sqlite_master` ile temel MESA tablo,
  named index, FTS5 ve trigger varlığını; `PRAGMA table_info` ile `nodes`
  sütun, primary-key, tür, `NOT NULL` ve default sözleşmesini fail-closed doğrular.
- Sadece bilinen `v0.3`, `v0.4`, `v0.5.0` ve `v0.5.1-v0.5.2` pre-Alembic
  parmak izleri offline adoption'a uygundur. Bilinmeyen veya revision iddia eden
  drift herhangi bir Alembic revision yazmadan reddedilir.
- Adoption açık operatör niyeti ister: `alembic -x mesa_legacy=adopt upgrade head`.
- Bridge ile base revision stamp tek `BEGIN IMMEDIATE` işlemi içindedir. Stamp
  başarısızsa bridge geri alınır. Sonraki revision başarısız olursa DB doğru
  biçimde base revision'da kalır; tekrar çalıştırma resume davranışıdır.
- Migration süreci sadece Alembic için `sqlite+pysqlite` kullanır. Runtime
  `aiosqlite` değişmedi. Startup'ta executor/event-loop deadlock'unu önlemek
  için migration aynı başlangıç çağrısında senkron yürütülür.

## Regresyon ve kanıt

Tüm komutlarda model/provider/dotenv kapalıydı.

| Seviye | Kanıt | Sonuç |
|---|---|---|
| E2 | fresh upgrade | 1 passed, 4.25 s |
| E2 | legacy v0.3/v0.4/v0.5 adoption | 3 passed, 4.86 s |
| E2 | pre-remediation + unknown/claimed drift | 3 passed, 4.72 s |
| E2/E3 CLI | PK/default drift + stamp rollback + CLI + startup | 4 passed, 4.65 s |
| Statik | Ruff, `py_compile`, tracked diff whitespace | geçti |

Bu dört dar çağrı test dosyasındaki 11 toplanan senaryonun tamamını kapsar.
Tek birleşik pytest çağrısı düşük kaynaklı ortamda çıktı üretmeden uzadı ve
durduruldu; ayrı kanıtlar başarılı olduğundan yeniden zorlanmadı.

Önceki ilgili `tests/test_recovery_contract.py` çağrısı dört senaryodan sonra
gerçek LanceDB/Kùzu reopen testinde tamamlanmış sonuç/exit üretmedi. Ağır test
burada tekrar çalıştırılmadı.

## Açık kapanış koşulları

`MIG-001` **Düzeltildi ancak doğrulanmadı**; release blocker kapatılmadı.

1. İzole CPU runner'da recovery/reopen kanıtı alınmalı.
2. Base-stamp sonrası later-revision hata durumunda operasyonel resume/recovery
   prosedürü kanıtlanmalı. Kod yanlış `head` stamp'lemez; bütün zinciri tek
   transaction'da geri alma iddiası yapılmamaktadır.
3. Clean CI olmadan `Verified resolved` veya production `GO` yazılmaz.

Docker, Ollama/GPU, dış provider, production, tam suite veya load testi bu
dalga kapsamında çalıştırılmadı. Commit/push oluşturulmadı.
