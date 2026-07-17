# Komut Günlüğü

Önemli her komut için tarih, amaç, komut, çalışma dizini, exit code ve sonuç kaydedilir. Secret içeren argüman ve değerler maskelenir; hassas çıktı yazılmaz.

| Tarih | Amaç | Komut | Çalışma dizini | Ortam | Exit code | Süre | Sonuç |
|---|---|---|---|---|---:|---|---|
| 2026-07-17 | Repository kökünü doğrulama | `pwd` | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Kök doğrulandı |
| 2026-07-17 | Branch ve çalışma ağacı durumunu doğrulama | `git status --short --branch` | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | `main`; kullanıcıya ait untracked öğeler bulundu ve değiştirilmedi |
| 2026-07-17 | Başlangıç commit’ini doğrulama | `git rev-parse HEAD` | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Commit kaydedildi |
| 2026-07-17 | Mevcut talimat/audit/rapor dosyalarını konumlandırma | `rg --files --hidden …` | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Kök `AGENTS.md` ve `.audit/` bulunmadı; `ARCHITECTURE.md` korundu |
| 2026-07-17 | Faz 0 başlangıç durumu | `pwd`; `git status --short --branch`; `git rev-parse HEAD` | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Kök, branch, commit ve beş untracked yol doğrulandı; üç kullanıcı yolu korundu |
| 2026-07-17 | Ağaç ve boyut envanteri | `find`/`du`/`awk` (yalnızca okuma) | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | 3 seviye ağaç, uzantılar, büyük dosyalar ve runtime/cache alanları çıkarıldı |
| 2026-07-17 | Bileşen/dependency/entry point envanteri | `rg --files`, `rg -n`, `sed -n` (yalnızca okuma) | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Python paketleri, Docker, benchmark, eval, API ve worker girişleri çıkarıldı |
| 2026-07-17 | Config ve dış servis envanteri | `.env.example` yalnızca değişken adları; kaynakta statik referans taraması | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Secret değeri okunmadan environment adları, storage ve provider referansları haritalandı |
| 2026-07-17 | Test, CI/CD ve dokümantasyon envanteri | Test dosyası sayımı, CI/githook/doküman başlık taraması | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Testler/CI yalnızca listelendi; hiçbir test veya pipeline çalıştırılmadı |
| 2026-07-17 | Faz 0 kayıtlarını güncelleme | Patch tabanlı audit doküman güncellemesi | `/home/yasin/Desktop/MESA` | Mevcut shell | 0 | Ölçülmedi | Yalnızca izin verilen altı audit dosyası güncellendi |

## Test çalıştırma kayıt şablonu

| Tarih | Komut | Çalışma dizini | Ortam | Exit code | Süre | Geçen | Başarısız | Atlanan | Hata özeti |
|---|---|---|---|---:|---|---:|---:|---:|---|
| — | — | — | — | — | — | — | — | — | — |
