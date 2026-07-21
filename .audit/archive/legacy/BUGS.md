# Tekrar Üretilebilen Hatalar

Bu dosya runtime, component, deterministic source-invariant veya iş mantığı düzeyinde tekrar üretilebilen failure kayıtları için kullanılır. Her kayıt kanıt seviyesini açıkça belirtir; non-runtime kanıt, doğrulanmış runtime bug sayılmaz. Kesinleşmemiş konular önce `FINDINGS.md` içinde uygun kanıt durumuyla tutulur.

## Hata şablonu

### BUG-XXX — Kısa başlık

| Alan | Değer |
|---|---|
| Belirti | — |
| Beklenen davranış | — |
| Gerçek davranış | — |
| Tekrar üretme adımları | — |
| Log | Secret ve kişisel veri maskelenmiş özet |
| Kök neden | — |
| Etkilenen bileşenler | — |
| Düzeltme | — |
| Regresyon testi | — |
| Doğrulama sonucu | — |

## Kayıtlar

Henüz tekrar üretilebilen hata kaydedilmedi.


## Faz 9 düzeltme dalgası 1

### BUG-001 — DLQ replay destructive clear ve tenant context kaybı

| Alan | Değer |
|---|---|
| Bulgu ID / durum | DLQ-001 / Partially fixed / Fixed but not verified |
| Kanıt seviyesi | E1 — deterministic source invariant |
| Runtime doğrulama | Not performed |
| Nihai durum | Partially fixed / Fixed but not verified |
| Önem | Kritik / P0 |
| Etkilenen modül | `mesa_memory.consolidation.loop` — `PersistentQueue`, `ConsolidationLoop.run_batch`, `start_dlq_worker` |
| Beklenen / gerçek | Replay item'ı başarıyla işlendiği doğrulanana kadar durable kalmalı ve tenant ile lookup yapılmalıydı. Önceki kod tüm queue'yu `clear()` ediyor, sonra default agent ile lookup yapıyordu. |
| Yeniden üretim / mevcut test kanıtı | İzole source invariant komutu düzeltme öncesi `destructive clear`, `agent_id` eksik producer/replay hatalarıyla exit 1 verdi. Faz 1.5 gate nedeniyle MESA importlu runtime test çalıştırılmadı. |
| Düzeltme | DLQ producer item'larına `agent_id` eklendi; replay legacy/eksik-agent item'ı silmeden atlar; sadece `run_batch` döndükten sonra seçili item'lar atomik dosya replace ile kaldırılır. Aynı instance append/rewrite `threading.Lock` ile korunur. |
| Regresyon testi | Static invariant: replay içinde `.clear()` yok; agent propagation ve selected-item acknowledgement var; `py_compile` geçti. |
| Kalan risk / dalga | Multi-process claim/lease, run_batch per-record outcome ve legacy item migration yok; Dalga 1 tamamlandı, kalanlar Deferred. |

### BUG-002 — Unmapped principal could self-grant session access

| Alan | Değer |
|---|---|
| Bulgu ID / durum | SEC-002 / Fixed but not verified |
| Belirti | Authenticated `principal-a` ile istenen `agent-b` için `/session/start` 403 yerine 200 dönüyordu. |
| Kök neden | Router, request body içindeki `agent_id` için herhangi bir caller-principal mapping kontrolü olmadan `grant_access` çağırıyordu. |
| Düzeltme | API key dependency principal context ekler; RBAC explicit `principal_agent_permissions` saklar; session start `SESSION_CREATE` kontrolünden önce grant vermez. |
| Kanıt / regresyon | Düzeltme öncesi deterministic target failure; düzeltme sonrası target + RBAC/session/router 30 test geçti. |
| Kalan doğrulama | E3 iki-principal HTTP, SDK/MCP contract, provisioning/migration ve diğer endpointlerin principal authorization dönüşümü eksik. |

## WAVE-001 clean restart update (2026-07-19)

`BUG-002` tarihsel WAVE-001 kaydı silinmedi. Clean-restart-01, mevcut patched source üzerinde E2 authorization davranışını yeniden doğruladı; bu bir fresh pre-fix runtime reproduction değildir. `SEC-002` canonical olarak açık P0/release blocker ve `Fixed but not verified` kalır; E3 ve cross-endpoint proof yoktur.

## WAVE-004 graph fixture classification

`tests/test_dao.py` içindeki 13 failure ürün bug’ı değildir: dokuz eksik async `insert_node` mock’u, dört tombstone-filtered neighbor mock ID’si uyuşmazlığıdır. Minimal fixture alignment sonrası 33 passed; ayrıntı `.audit/remediation/GRAPH_FIXTURE_FAILURE_ANALYSIS.md` içindedir.


## Continuation E3 matrix update — 2026-07-19

Mevcut remediation altında iki somut sınır düzeltildi: authenticated session `context`/`end`/session-scope `purge` artık persisted principal-session binding ister; JSONL parser malformed satırı hashli quarantine sidecar’a alıp önceki geçerli kayıtları fail-stop etmeden korur. Aynı `queue_id` duplicate append reddedilir. Bunlar mevcut `SEC-002` ve `DLQ-001` kapsamındadır; yeni bug/finding ID üretilmedi ve E3 tam kabul iddiası yoktur.


## Continuation contract/alignment/crash update — 2026-07-19

Async SDK `Authorization: Bearer` gönderirken API `X-API-Key` istediği için gerçek purge route 401 veriyordu; ardından SDK response modeli mevcut lowercase route body’siyle uyuşmadığı için validation hatası veriyordu. `AsyncMesaClient` headerı ve `MemoryPurgeResponse` gerçek public route/README body’siyle minimal hizalandı; 401 pre-fix ve post-fix route regression kaydedildi. Yeni finding ID oluşturulmadı; canonical sayılar değişmez.

## Master closure bug reconciliation — 2026-07-20

- `BUG-001` / `DLQ-001`: durable claim/lease, per-record receipt-before-ACK, restart reconciliation ve production consumer doğrulamasıyla `Verified resolved`.
- `BUG-002` / `SEC-002`: principal→agent/session binding ve HTTP/SDK/MCP negatif-pozitif kanıtıyla `Verified resolved`.
- Yeni bug ID açılmadı. `dummy.txt` CWD debug task’ı mevcut `ARCH-003` altında kaldırıldı ve 4-test negative regression geçti.
