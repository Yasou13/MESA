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
