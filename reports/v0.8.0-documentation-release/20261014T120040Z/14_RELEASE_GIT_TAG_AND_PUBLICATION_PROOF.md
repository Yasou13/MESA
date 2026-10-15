# Git, etiket ve yayın kanıtı

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Başlangıç dalı `fix/mesa-core-comprehensive-retrieval-hardening`, SHA `8ddd2dc04cf697fe237e7a49e391543c01182d83`. Doğrulanmış ilk docs commit `b83aff4188c1a59e8294d5f6587451a1bd08160a`. Commit/push/PR kesin sonuçları sonradan bu rapora gözlenen değerlerle eklenecektir; henüz gözlenmeyen yayın PASS sayılmaz.

2026-10-15 GitHub GET ile default main: `54d11678b64f7801934a6cd265db2083981a9f54` (önceki PR107 merge). Açık aynı-head PR listesi `[]`. Son yayın v0.7.1, published_at `2026-07-29T08:36:19Z`, doğrulanmış URL https://github.com/Yasou13/MESA/releases/tag/v0.7.1 . v0.8.0 remote tag yok; son readback ayrıca kaydedilecektir.

Branch API protected=false döndü; ayrıntılı protection endpoint'i connector integration403 nedeniyle okunamadı. Bunun policy/review atlama yetkisi olduğu çıkarılmadı. Yerel signingkey ayarı bulunmaması signing key olmadığına dair kesin kanıt sayılmadı; imza NOT_VERIFIED. Repo policy signed annotated tag ister.

**RELEASE_COMMIT_SHA=NOT_CREATED; VERSION_METADATA=NOT_UPDATED; RELEASE_TAG=NOT_CREATED; GITHUB_RELEASE_URL=NOT_PUBLISHED.** Release notes dosyası yayın kanıtı değildir. v0.8.0 tag push, merge veya GitHub Release mutation yapılmadı. 13 raporundaki genuine blockerlar nedeniyle yayın durduruldu; sıradan repair branch commit/push ve draft PR review için hazırlanır.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.
