# v0.8.0 aday notları ve geçiş

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Yayın metinleri: `docs/release-notes-v0.8.0.md` ve `docs/migration-v0.8.0.md`. **Adaydır, yayın değildir.** `pyproject.toml` ve çalışan/wheel metadata 0.7.1 olarak tutarlı kaldı. `CHANGELOG.md` yalnız Unreleased bölümünde docs/örnek düzeltmeleri ile dashboard iki transitif security patch içerir. Eski release bölümleri byte karşılaştırmasıyla korunur.

Notların kapsamı: canonical SQLite ownership ve derived LanceDB/Kùzu; V3 compatibility ile V4 canonical ayrımı; runtime/config/CLI/MCP kontrol yüzeyi; RRF ve graph sınırları; migration/backup/rollback sorumluluğu; source-backed hata durumları ve unsupported özellikler. Yeni model başarısı, benchmark skoru, hukuk sertifikası veya production GO iddiası yok.

Geçişte mevcut V3 storage üstüne V4 yazılmaz. Ayrı storage root, offline backup, v4 rebuild, parity/health kapısı ve geri dönüş kanıtı gerekir. Auth principal/workspace/dataset sınırları korunur. Insert için title, source/revision hash ve idempotency/terminal state doğru kullanılmalı. SDK legal_identity parametresi sunmadığından HTTP özelliği SDK'da varmış gibi anlatılmaz. Provider/dimension kimliği depolanan embedding ile eşleşmeli.

Sonraki operator: 13 raporundaki kapıları aynı hedef SHA için tamamlar; sonra repository politikasına göre 0.8.0 metadata/datelı changelog değişikliğini review eder, CI'ı geçirir, temiz default/release checkout'ta wheel/SBOM ve signed tag doğrular. Şimdiden sürüm değiştirmek veya feature branch'i stable etiketlemek eksik kanıtı gidermez.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.
