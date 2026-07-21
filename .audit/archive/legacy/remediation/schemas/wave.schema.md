# Wave Schema

Her `waves/WAVE-XXX.md` dosyası WAVE_TEMPLATE’i kullanır. Zorunlu bölümler metadata, scope, canonical findings, kök neden, bağımlılıklar, reproduction/failing evidence, remediation planı, testler, runtime gate, etkiler, rollback, audit reconciliation ve wave result’tır.

İzinli sonuçlar: `VERIFIED_COMPLETE`, `FIXED_NOT_VERIFIED`, `PARTIALLY_COMPLETE`, `BLOCKED`, `STOPPED_FOR_DECISION`, `ROLLED_BACK`, `FAILED_SAFE`.

Wave sonucu canonical finding’i otomatik kapatmaz; kapanış POLICY’deki kanıt zinciri ve audit reconciliation gerektirir.
