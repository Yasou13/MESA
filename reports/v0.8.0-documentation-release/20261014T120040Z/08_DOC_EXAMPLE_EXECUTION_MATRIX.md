# Doküman örneklerinin çalıştırma matrisi

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Kanıt türleri: EXECUTED gerçek komut; SOURCE/SYNTAX kaynak ve sözdizimi; NOT_EXECUTED yürütülmeyen kullanım. Çalıştırılmamış örnek PASS sayılmaz.

| Yüzey / örnek | Komut veya kanıt | Exit / gözlem | Durum |
|---|---|---|---|
| README → getting-started → V3 istemci | `.venv/bin/python -O scripts/documentation_smoke.py` | 0, ilk boot ve yeniden boot; 18.13s | EXECUTED_PASS |
| Auth ve negatif schema | Aynı smoke: anahtarsız 401, auth health 200, bilinmeyen session 403/404, hatalı request 422 | 0; `smoke-proof.json` | EXECUTED_PASS |
| Diskte saklanan önceki session/log/evidence | Restart sonrası yeni kayıt oluşturmadan önce saklanan veriyi sorgulayan smoke | 0 | EXECUTED_PASS |
| Optimizasyon güvenliği | `python -O` içinde `require(False)` RuntimeError bekler | 0; `optimized-negative-check.txt`, 0.33s | EXECUTED_PASS |
| V4 canonical insert örneği | Model-disabled yerel deneme 503 canonical_processing_unavailable; provider gereği dokümana işlendi | Başarılı ingestion gözlenmedi | NOT_EXECUTED_WITH_REAL_PROVIDER |
| V4 mutation/search/graph/context | 38 shard içindeki API/SDK/DAO contract testleri | 1948 toplam test; 10 raporu / manifest | TEST_CONTRACT_PASS; canlı provider proof değil |
| `.env.example` | MesaConfig ile örnek yapılandırma kuruldu; tenant/global queue sınırları uyumlu | 0 | EXECUTED_PASS |
| Markdown Python/Bash/YAML/JSON/TOML | `python scripts/check_documentation.py` | 0; notebook-only 3 fence hariç 58 fence kontrolü | SYNTAX_PASS |
| Colab ve gerçek modeller | Notebook magic olarak sınıflandırıldı; model indirme/VM/real inference yapılmadı | N/A | NOT_EXECUTED |
| Docker Compose deploy / install komutları | Dosya/topology/contract testleri; gerçek container deployment yürütülmedi | N/A | SOURCE_AND_TEST_ONLY |
| Dashboard | `npm ci --ignore-scripts`, `npm run build`, `npm run lint` | tümü 0 | EXECUTED_PASS |
| Showcase scriptleri | `node --check` iki JS dosyası | 0 | SYNTAX_PASS; görselleştirici simülasyon |
| Wheel kurulum/import | `uv pip install --no-deps --target /tmp/mesa-v080-wheel-smoke <wheel>` ve `python -I` | 0, üç modül bağımsız wheel dizininden import, 0.7.1 | EXECUTED_PASS; mevcut .venv bağımlılıkları |
| Backup/restore/rebuild | Unit/contract test kapsamı; production staging rehearsal yok | N/A | NOT_EXECUTED_RELEASE_REHEARSAL |

SDK `wait_until_committed` terminal başarısızlıkta da dönebilir; örnek COMMITTED sonucunu ayrıca denetler. V3 `agent_id` compatibility scope ile V4 principal/workspace/dataset aynı güvenlik garantisi değildir. Örneklerdeki release-placeholder gerçek anahtar değildir. Hiçbir örnek resmî Profile B / hukuk doğruluğu sertifikası değildir.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.
