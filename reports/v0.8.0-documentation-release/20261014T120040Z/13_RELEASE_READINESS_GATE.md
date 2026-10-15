# Yayın hazırlığı kapıları

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

**Toplam sonuç: BLOCKED; production NO-GO.** Politika kaynağı `docs/release.md`; kapılar azaltılmadı. Eksikler yetkisizlik iddiası değildir; gerçek kanıt/review eksikliğidir.

| Kapı | Gözlem | Durum / sahip |
|---|---|---|
| Kullanıcı rehberleri/yerel bağlantı/syntax | 09, final-checks | PASS scoped |
| Her public/internal method docstring proof | 184/1093 envanter, kritik risk noktaları incelendi; tüm methodlar için ayrı kanıt yok | NOT_VERIFIED; Core maintainer |
| Makul yerel regresyon | 1948 pass, 38 shard | PASS scoped |
| Quality / dependencies | Ruff/Mypy/layer/uv pass; Black scoped gate ayrı, genel eski format borcu raporlanır | PASS scoped; geniş borç release operatörünce çözülmeli |
| Auth/scope/negatif/schema ve kalıcılık smoke | 08, gerçek V3 restart smoke | PASS scoped |
| V4 gerçek provider boot/restart/token/maliyet | Gerçek provider çalıştırılmadı | BLOCKED; staging/operator |
| Production kapasite/crash/concurrency | contract testleri production benzeri kanıt yerine geçmez | BLOCKED; operator |
| Backup→restore→rebuild→parity→cutover rehearsal | Release SHA için yeni staging kanıtı yok | BLOCKED; storage owner |
| 24 saat soak | Yeni mühürlü başlangıç/bitiş/image/config/health kanıtı yok | BLOCKED; operator |
| Benchmark dataset/RRF lane ablation | Aynı release SHA'ya bağlı bağımsız yeni kanıt yok; TEST/qrels'e dokunulmadı | BLOCKED; benchmark owner |
| Security/secret policy | Statik içerik kontrolü + npm0; TruffleHog/Trivy/image scan aynı SHA'da yok | PARTIAL / BLOCKED external gate |
| Clean release checkout / version | Kullanıcının başlangıçtaki silinmiş journal ve prompt'u korunuyor; sürüm0.7.1 | BLOCKED release checkout |
| Resmî release SHA / review / CI | Repair branch; default main'de yeni docs commitleri henüz review edilmedi | BLOCKED; maintainer |
| v0.8.0 tag / signing / SBOM / OIDC | Tag yok; trusted keyring/verified attestation yok | NOT_CREATED / BLOCKED |
| Historical / cross-repo | 162 başlangıç hash'i karşılaştırıldı; kullanıcı silmesi aynı; Core dışına yazılmadı | PASS |

Owner eylem sırası: reviewable PR → aynı release SHA için tüm mandatory CI ve bağımsız staging/benchmark/DR/24h kanıtları → kalan method docstring/format borcu → metadata0.8.0/datelı changelog → CI tekrar → temiz default/release checkout'ta signed annotated tag/preflight → tag push → wheel/SBOM OIDC digest doğrulama → gerçek GitHub Release → URL/tag/SHA readback. Hiçbir aşama eski raporu yeni PASS olarak kullanamaz. Official Profile B **NOT_RERUN_AND_NOT_CERTIFIED**; izin verilen çalışma sınırı korunur.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.
