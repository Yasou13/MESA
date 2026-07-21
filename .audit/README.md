# MESA Audit Çalışma Sistemi

Bu dizin, MESA’nın kapsamlı analiz, debugging ve production-readiness çalışması için kalıcı kanıt ve karar kaydıdır. Uygulama kodu yerine çalışma izini saklar; her kayıt Türkçe ve secret içermeyecek şekilde tutulur.

## Kullanım

Çalışma yalnızca `CURRENT_PHASE.md` içindeki aktif fazda ilerler. Bir fazın kanıtları, bulguları, testleri, kararları ve blocker’ları ilgili dosyalara kaydedilir. Faz çıkış raporu tamamlandıktan sonra kullanıcı onayı olmadan sonraki faza geçilmez.

## Faz sırası

`Faz 0 → Faz 1 → Faz 2 → Faz 3 → Faz 4 → Faz 5 → Faz 6 → Faz 7 → Faz 8 → Faz 9 → Faz 10 → Faz 11 → Faz 12 → Faz 13 → Faz 14`

## Dosya rehberi

| Dosya | Görevi |
|---|---|
| `CURRENT_PHASE.md` | Aktif faz, durum ve geçiş kapısı |
| `BASELINE.md` | Başlangıç Git/ortam/build/test ölçümleri |
| `INVENTORY.md` | Repo bileşen envanteri |
| `SYSTEM_MAP.md` | Bileşenler ve bağımlılık ilişkileri |
| `DATA_FLOWS.md` | Kritik uçtan uca veri akışları |
| `FINDINGS.md` | Kanıt standardına uygun tüm teknik bulgular |
| `BUGS.md` | Tekrar üretilebilen runtime ve iş mantığı hataları |
| `FIX_PLAN.md` | Önceliklendirilmiş düzeltme planı |
| `TEST_MATRIX.md` | Risk, gereksinim ve test kapsamı eşlemesi |
| `COMMAND_LOG.md` | Önemli komutlar ve sonuçları |
| `CHANGELOG_AUDIT.md` | Audit sırasında yapılan değişiklikler |
| `DECISIONS.md` | ADR-benzeri teknik kararlar |
| `BLOCKERS.md` | Production’a çıkışı engelleyen doğrulanmış sorunlar |
| `DEFERRED.md` | Ertelenen ancak izlenmesi gereken işler |
| `PRODUCTION_READINESS.md` | Nihai GO/CONDITIONAL GO/NO-GO değerlendirmesi |

Mevcut kayıtlar silinmez; yeni bilgi eklenir, düzeltmeler tarihçeli biçimde belirtilir. Bulgu kimlikleri tekrar kullanılmaz.

## Canonical durum sözlüğü

Kanonik finding, blocker ve remediation kayıtlarında aşağıdaki durumlar kullanılır. Tarihsel satırlar yalnız tarihçe olarak korunur; kanonik indekse kaynaklık etmez.

| Durum | Anlamı |
|---|---|
| Open | Açık, henüz yeterli doğrulama/triage tamamlanmamış kayıt |
| Confirmed open | Kanıtla doğrulanmış ve açık kayıt |
| In remediation | Düzeltme uygulaması sürüyor |
| Partially fixed | Düzeltmenin yalnız bir bölümü uygulanmış |
| Fixed but not verified | Düzeltme var, gerekli regresyon/runtime doğrulaması yok |
| Verified resolved | Gerekli kanıtla kapanmış kayıt |
| Mitigated | Risk azaltılmış, kök neden henüz kapanmamış |
| Deferred | Bilinçli olarak ileri faza ertelenmiş kayıt |
| Blocked | Dış bağımlılık veya giriş kapısı nedeniyle ilerleyemeyen iş |
| False positive | Kanıtla geçersiz olduğu gösterilmiş kayıt |
| Duplicate | Başka bir kanonik kaydın tarihçesi veya tekrarı |
| Superseded | Daha yeni kanonik kayıt tarafından geçersiz kılınmış tarihsel kayıt |
| Not tested | İlgili test çalıştırılmamış |
| Not verified | İddia veya düzeltme için yeterli kanıt yok |

## Remediation wave runner

Remediation runner source: `.audit/remediation/`. Checkpoint ve aktif remediation referansı: `.audit/remediation/STATE.md`. Bu altyapı Faz 14 kararını değiştirmez; varsayılan çalışma modu `supervised`’dır.
