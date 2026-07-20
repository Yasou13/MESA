# Başlangıç Baseline’ı

Bu kayıt, çalışma ortamı ve doğrulama sonuçlarının değişmez başlangıç noktasını tutar. Ölçülmemiş alanlar sonuç uydurulmadan işaretlenir.

| Alan | Değer | Kanıt / Not |
|---|---|---|
| Kayıt tarihi | 2026-07-17 | Audit sistemi kurulumu sırasındaki güvenlik kontrolü |
| Repository kökü | `/home/yasin/Desktop/MESA` | `pwd` |
| Aktif branch | `audit/production-readiness` | `git status --short --branch` |
| Başlangıç commit’i | `8798abc90979401d4785cc25d4627517860cb959` | `git rev-parse HEAD` |
| Git çalışma ağacı | Commit edilmemiş değişiklik yok; 5 untracked yol mevcut | `git status --short --branch`; ayrıntı aşağıda |
| İşletim sistemi / ortam | Henüz ölçülmedi | — |
| Runtime sürümleri | Henüz ölçülmedi | — |
| Dependency durumu | Henüz ölçülmedi | — |
| Build sonucu | Henüz ölçülmedi | Build çalıştırılmadı |
| Test sonucu | Henüz ölçülmedi | Test çalıştırılmadı |
| Lint sonucu | Henüz ölçülmedi | Lint çalıştırılmadı |
| Type-check sonucu | Henüz ölçülmedi | Type-check çalıştırılmadı |
| Runtime sonucu | Henüz ölçülmedi | Servis başlatılmadı |

## Faz 0 başlangıç kontrolü

| Konu | Doğrulanmış durum |
|---|---|
| Repository kökü | `/home/yasin/Desktop/MESA` |
| Aktif Git branch | `audit/production-readiness` |
| Commit hash | `8798abc90979401d4785cc25d4627517860cb959` |
| Takipli çalışma ağacı | Değişiklik yok (`git status --short` yalnızca `??` girdileri döndürdü) |
| Audit altyapısı | `.audit/` ve `AGENTS.md` önceki kurulumdan kalan untracked çalışma dokümanları |
| Önceden mevcut kullanıcı dosyaları | `cold_path_trace.txt`, `dummy.txt`, `results/mesa_client/contradiction_stress_200_v2_seed42/` |
| Kullanıcı dosyalarına işlem | Hiçbirine dokunulmadı, silinmedi veya commit edilmedi |

Bu baseline’da build, test, lint, type-check veya runtime sonucu yoktur; Faz 0 kapsamında hiçbiri çalıştırılmadı.


## Baseline güncelleme kaydı

| Tarih | Alan | Önceki değer | Yeni değer | Kanıt |
|---|---|---|---|---|
| 2026-07-17 | Faz 0 güvenlik ve kapsam baseline’ı | Başlatılmamış şablon | Statik keşif kanıtları eklendi | `COMMAND_LOG.md` |
