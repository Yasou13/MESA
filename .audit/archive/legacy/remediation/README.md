# MESA Remediation Wave System

## Amaç

Bu dizin, production blocker’larını kontrollü remediation wave’leriyle kapatmak için kalıcı çalışma altyapısıdır. Her wave failing evidence, minimum fix, regression ve gerekli runtime kanıtını üretir; oturumlar arası checkpoint ile devam eder ve canonical audit kayıtlarını güncel tutar.

## Çalışma modeli

`POLICY.md` oku → `STATE.md` oku → Git güvenlik kontrolü → aktif wave varsa devam et → yoksa `QUEUE.md` içinden sıradaki Ready wave’i seç → plan → reproduce → patch → target test → regression test → runtime gate → reconcile → evidence persist → checkpoint.

## Canonical ve tarihsel ayrımı

- `waves/WAVE-XXX.md`: tarihsel wave raporu.
- `evidence/WAVE-XXX/`: ham veya maskelenmiş özet kanıtlar.
- `.audit/FINDINGS.md`: güncel canonical finding durumu.
- `.audit/BLOCKERS.md`: güncel production blocker durumu.
- `.audit/PRODUCTION_READINESS.md`: nihai canonical karar.
- `STATE.md`: otomasyon checkpoint’i; production kararı değildir.

## Çalışma modları

- `supervised`: her wave sonunda durur.
- `bounded_auto`: açıkça belirlenmiş sınırlı sayıda wave yürütür.
- `sequential_auto_with_controlled_recovery`: dependency sırasını ve kanıt kapılarını koruyarak bir wave yürütür; yalnız belgelenmiş R1/R2/uygun R3 regresyonlarını bounded biçimde giderir.
- `full_auto`: kanıt, dependency veya ürün kararı kapılarını atlayan sınırsız otomasyon olarak yasaktır.

Bu laboratuvar run'ında mod `sequential_auto_with_controlled_recovery` olabilir. Ürün/mimari kararını otomatikleştirmez; WAVE-000 karar kaydı ve güvenli checkpoint olmadan WAVE-001 açılmaz.
