# Git, etiket ve yayın kanıtı

Sonuç tarihi (UTC): 2026-10-15T17:19:02.149696+00:00. Ortam: Linux, Python 3.10.12, uv 0.10.8; yerel testler model-disabled, dış provider kapalı. Başlangıç SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`; dokümantasyon commit: `b83aff4188c1a59e8294d5f6587451a1bd08160a`; bağımsız inceleme düzeltmeleri bu commit üzerine uygulanmıştır. Resmî release commit’i oluşturulmadı.

Başlangıç dalı `fix/mesa-core-comprehensive-retrieval-hardening`, SHA `8ddd2dc04cf697fe237e7a49e391543c01182d83`. Doğrulanmış ilk docs commit `b83aff4188c1a59e8294d5f6587451a1bd08160a`. Commit/push/PR kesin sonuçları sonradan bu rapora gözlenen değerlerle eklenecektir; henüz gözlenmeyen yayın PASS sayılmaz.

2026-10-15 GitHub GET ile default main: `54d11678b64f7801934a6cd265db2083981a9f54` (önceki PR107 merge). Açık aynı-head PR listesi `[]`. Son yayın v0.7.1, published_at `2026-07-29T08:36:19Z`, doğrulanmış URL https://github.com/Yasou13/MESA/releases/tag/v0.7.1 . v0.8.0 remote tag yok; son readback ayrıca kaydedilecektir.

Branch API protected=false döndü; ayrıntılı protection endpoint'i connector integration403 nedeniyle okunamadı. Bunun policy/review atlama yetkisi olduğu çıkarılmadı. Yerel signingkey ayarı bulunmaması signing key olmadığına dair kesin kanıt sayılmadı; imza NOT_VERIFIED. Repo policy signed annotated tag ister.

**RELEASE_COMMIT_SHA=NOT_CREATED; VERSION_METADATA=NOT_UPDATED; RELEASE_TAG=NOT_CREATED; GITHUB_RELEASE_URL=NOT_PUBLISHED.** Release notes dosyası yayın kanıtı değildir. v0.8.0 tag push, merge veya GitHub Release mutation yapılmadı. 13 raporundaki genuine blockerlar nedeniyle yayın durduruldu; sıradan repair branch commit/push ve draft PR review için hazırlanır.

Doğrulanan düzeltme commit’i: `f627a93ae55a5ce5f0819efddf1a357e54db91f2`. Test dosyaları bu SHA’ya kadarki runtime semantiği ile aynıdır; son değişiklikler açıklama/örnek kontrolleri ve patch lockfile düzeyindedir.

## Gerçek push ve PR readback (2026-10-15T17:26:32.348353+00:00)

`git push origin HEAD:refs/heads/fix/mesa-core-comprehensive-retrieval-hardening` exit0; normal fast-forward8ddd2dc→e643322. `git ls-remote` exit0 ile uzak repair branch `e643322f6625ebf45f7645d6bcc40ad85a85fbcc` ve main `54d11678b64f7801934a6cd265db2083981a9f54` doğrulandı. v0.8.0 ref çıkmadı.

Commit zinciri: b83aff4 source-backed guides; f627a93 independent corrections/smoke/security patches; bfda472 yeni Türkçe raporlar; e643322 yeni CSV LF normalizasyonu. Değişiklikler commitlenip push edildi. Kullanıcı journal silmesi ve prompt commitlenmedi. İlk CSV check CRLF nedeniyle uyarı verdi; takip commitinde LF yapıldı ve son check0 oldu. Tarihsel raporlar değiştirilmedi.

Taslak PR **https://github.com/Yasou13/MESA/pull/108** main hedefiyle oluşturuldu (open,draft=true,head=e643322f6625ebf45f7645d6bcc40ad85a85fbcc); chat'e artifact olarak eklendi. GitHub connector create PR403 döndü; aynı GitHub host için mevcut standart git credential helper ve REST API ile başarılı oluşturuldu. Credential gösterilmedi/yazılmadı. Bu bir auth kaynaklı release blocker değildir.

PR oluşturma anında aynı head için MESA CI run38777213666 ve MESA external release gates run38777212941 queued/null; PASS sayılmadı. Sonraki report-only commit yeni CI tetikleyebilir; 24h/provider/staging/signing/attestation eksikleri bağımsız olarak yayını engeller. Merge/release/tag mutation yapılmadı.

`publication-proof.json` bu gözlenmiş kod/rapor push'unu sabitler. Kanıtı taşıyan takip commitinin kendi SHA'sı aynı dosyaya döngüsel olarak yazılmaz; son push bağımsız readback ile final teslimde belirtilir.
