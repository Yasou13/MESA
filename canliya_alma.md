# ESA — Canlıya Alma Öncesi Doğrulama (Go-Live) Master Promptu

**Amaç:** "Tüm açıklar kapatıldı" iddiasını **bağımsız kanıtla** doğrulamak — kod okuyarak değil, çalıştırıp somut çıktı üreterek. Bu prompt hiçbir yerde "muhtemelen düzelmiştir" kabul etmez; her madde bir test + kanıt + PASS/FAIL kararı gerektirir.

**Çıktı dosyası:** `GO_LIVE_REPORT.md`. Fazlar sırayla işlenecek, her fazın sonunda `>>> FAZ G-N TAMAMLANDI — sonuç: PASS/FAIL/KISMEN` yaz, onay bekle.

---

## GENEL TALİMATLAR

1. Her testin sonucu şu şablonla yazılacak:

```
   ### [G-N.M] Test Adı
   - **Kontrol Edilen Bulgu/Alan:** (ör. R-19, soak test, retention worker)
   - **Test Yöntemi:** ne çalıştırıldı, hangi komut/senaryo
   - **Beklenen Sonuç:** ...
   - **Gerçek Sonuç:** [ham çıktı/log/sayı]
   - **Karar:** ✅ PASS / ❌ FAIL / ⚠️ KISMEN (nedeni)
```

2. Bir test FAIL çıkarsa, o alanı **canlıya çıkış engelleyici (blocker)** olarak işaretle ve sonraki faza geçmeden önce bunu ayrıca vurgula — ama diğer bağımsız fazları çalıştırmaya devam et (birini beklerken diğerini test edebilirsin), sadece **FAZ G-8'de (nihai karar)** tüm blocker'lar kapanmadan "GO" verme.
3. Varsayım yok — her PASS bir komutun gerçek çıktısına dayanmalı.

---

## FAZ G-1 — "Kapandı" İddiasının Bağımsız Doğrulanması

Bu, en kritik fazdır: `FIX_REPORT.md`'deki her Kritik/Yüksek bulguyu (R-01→R-19 öncelikli) tek tek yeniden test et — düzeltmeyi yapan agent'ın kendi beyanına güvenme.

1. `FIX_REPORT.md`'yi aç, her R-ID için "Adım 1 Sonucu" alanına bak. Kanıt olarak somut bir test/komut/log referansı yoksa (sadece "✅ Doğrulandı" yazıp kanıt yoksa), o bulguyu **kendin yeniden test et**.
2. Özellikle şu 3'ünü mutlaka yeniden koştur (yüksek risk):
    - **R-19 (payload kaybı):** `mesa_client/langchain.py` ve `mesa_mcp/server.py` üzerinden gerçek bir arama yap, dönen içeriğin artık sadece `entity_name` değil tam `content_payload` olduğunu göster.
    - **R-03 (saga ihlali):** `dao.py`'de bir `update()` çağrısı sırasında LanceDB tarafını yapay olarak başarısız kıl (mock/exception enjekte et), SQLite tarafının commit edilmediğini kanıtla.
    - **R-10 (MCP tenant spoofing):** MCP tool'una `agent_id` içeren bir prompt injection senaryosu dene, artık `MESA_AGENT_ID` env değişkeninden geldiğini ve LLM'in bunu değiştiremediğini göster.
3. **Benchmark kanıtı (özellikle önemli):** MESA client'ı ile taze bir `config_multi_hop.yaml` koşumu yap. Yeni üretilen `report_<run_id>.md`'de Hit@1/Hit@3/Hit@5/MRR/nDCG değerlerinin, eski `results/mesa_client/comprehensive_multihop_only/` altındaki **%0.00** değerlerinden farklı (gerçek, sıfırdan büyük) olduğunu göster. Bu, context-ID bug'ının gerçekten düzeldiğinin tek somut kanıtıdır.

`GO_LIVE_REPORT.md`'ye "## FAZ G-1 — Bağımsız Doğrulama" başlığıyla, her yeniden test için yukarıdaki şablonla yaz.

---

## FAZ G-2 — Tam Regresyon Suite'i (Bir Arada)

1. `tests/` altındaki **tüm** test suite'ini (802 test) tek seferde çalıştır: `pytest tests/ -v --tb=short`.
2. Toplam PASS/FAIL sayısını, varsa FAIL eden testlerin tam adını ve hata mesajını kaydet.
3. Özellikle `test_kuzu_isolation.py`, `test_rbac_leak.py`, `test_storage_unification.py` (RLS/Saga ile ilgili kritik testler) ayrı ayrı vurgulanarak sonucu yazılsın.
4. FAIL varsa: bu bir blocker'dır — hangi düzeltmenin (R-ID) bu regresyona sebep olduğunu tespit et (`git blame`/değişiklik geçmişi ile).

`GO_LIVE_REPORT.md`'ye "## FAZ G-2 — Tam Regresyon" başlığıyla yaz.

---

## FAZ G-3 — Secrets ve Ortam Hijyeni

1. `git log --all --full-history -- .env` ve `git log -p | grep -i "api_key\|secret"` (veya `git-secrets`/`trufflehog` varsa) ile geçmişte commit edilmiş bir secret olup olmadığını tara.
2. `.gitignore`'da `.env` olduğunu doğrula; ayrıca `.env.example`'da gerçek bir key kalıp kalmadığını kontrol et.
3. Production config'lerinde (`config*.yaml`) `${VAR}` şeklinde placeholder olan her alan için: kod tabanında (`config.py`) bunun gerçekten `os.path.expandvars` veya eşdeğeriyle genişletildiğini doğrula — genişletme yoksa, bu placeholder'lar literal string olarak gidiyor demektir (benchmark'ta `${ZEP_API_KEY}` için bulduğumuz sorunun production config'lerinde bir benzeri olup olmadığını kontrol et).
4. Demo UI (`demo/`) prod Docker imajına dahil mi? `docker build` sonrası image içinde `demo/` klasörünün var olup olmadığını `docker run --rm <image> ls` ile kontrol et — varsa ve içinde client-side API key varsa **blocker**.

`GO_LIVE_REPORT.md`'ye "## FAZ G-3 — Secrets ve Ortam" başlığıyla yaz.

---

## FAZ G-4 — Veri Katmanı: Migration ve Backup

1. `migrate_raw_logs_agent_id.py` ve `migrate_to_kuzu.py`'yi **production verisinin bir kopyası** (staging/test ortamı) üzerinde dry-run modunda çalıştır (script destekliyorsa `--dry-run`, desteklemiyorsa küçük bir örnek veri setiyle gerçek koşum). Çalışma öncesi/sonrası kayıt sayısını karşılaştır, veri kaybı olmadığını kanıtla.
2. Aynı script'i **iki kez art arda** çalıştır (idempotency testi) — ikinci çalıştırmada veri bozulması/duplikasyon olup olmadığını kontrol et.
3. Backup prosedürünü uçtan uca test et: SQLite + LanceDB + KùzuDB'nin üçünü birden yedekle (ilgili worker'ları/API'yi kısa süreliğine durdurarak veya WAL checkpoint'i bekleyerek), sonra bu yedekten **gerçekten geri yükleme** yap (ayrı bir ortamda), sistemin restore sonrası çalıştığını doğrula. Sadece "yedek dosyası oluştu" yeterli değil — restore edilip çalıştığı kanıtlanmalı.
4. Retention worker'ın gerçekten aktif olduğunu doğrula: bir test kaydını sil (soft-delete), retention süresini kısalt (test config'i ile), worker'ın belirtilen sürede kaydı hard-delete ettiğini logdan/veritabanından göster.

`GO_LIVE_REPORT.md`'ye "## FAZ G-4 — Veri Katmanı" başlığıyla yaz.

---

## FAZ G-5 — Gözlemlenebilirlik

1. `/metrics` endpoint'ine kimlik doğrulamasız bir `curl` isteği at — 401/403 dönmesi gerekiyor (dönmüyorsa **blocker**, güvenlik açığı).
2. Prometheus'un gerçekten bu endpoint'i scrape ettiğini (bir Prometheus/Grafana instance'ı varsa dashboard'da veri göründüğünü, yoksa en azından `curl localhost:PORT/metrics` çıktısında beklenen metriklerin (`request_count`, `saga_failure_total` vb.) dolu olduğunu) göster.
3. Şu 4 alarmın tanımlı olduğunu doğrula (varsa alerting config dosyasından, yoksa manuel kontrol listesi olarak işaretle ve eksikse şimdi ekle): API error rate, dual-write saga hata oranı, disk doluluğu, ingestion/queue backlog boyutu.
4. Yapay olarak bir hata tetikle (ör. LanceDB bağlantısını geçici kapat) ve ilgili alarmın/logun gerçekten tetiklendiğini göster.

`GO_LIVE_REPORT.md`'ye "## FAZ G-5 — Gözlemlenebilirlik" başlığıyla yaz.

---

## FAZ G-6 — Yük ve Dayanıklılık Kanıtı

1. `mesa_evals/soak_test.py`'yi gerçek anlamda uzun süre (en az birkaç saat, ideal olarak gece boyu) çalıştır. RSS/bellek kullanımını başlangıç ve bitişte karşılaştır — sürekli artan bir eğri (leak) varsa **blocker**.
2. `mesa_evals/load_test.py`'yi beklenen prod trafiğine yakın bir RPS ile çalıştır. P95/P99 latency ve hata oranını kaydet, kabul edilebilir eşiklerle (önceden belirlenmiş SLA varsa onunla, yoksa `gatekeeper.py`'deki TTFT limitiyle) karşılaştır.
3. Yük testi sırasında FAZ G-5'teki metriklerin (queue backlog, error rate) doğru şekilde yükseldiğini/normale döndüğünü gözlemle — gözlemlenebilirlik katmanının yük altında da çalıştığını kanıtlar.

`GO_LIVE_REPORT.md`'ye "## FAZ G-6 — Yük ve Dayanıklılık" başlığıyla yaz.

---

## FAZ G-7 — Kademeli Çıkış ve Geri Dönüş Planı

1. Rollback prosedürünü **gerçekten dene**: bir önceki versiyona (imaj/tag) geç, sistemin ayağa kalktığını ve veri kaybı olmadığını doğrula. Kaç dakika sürdüğünü ölç — bu senin gerçek "rollback süresi" SLA'ndır, tahmini değil.
2. Eğer bu rollback bir migration'ı (FAZ G-4'teki scriptler) geri almayı gerektiriyorsa, bunun için de bir geri-alma (down-migration) adımı var mı kontrol et — yoksa bu bir blocker olarak işaretlenmeli (migration'lar geri dönüşsüzse rollback planı eksik demektir).
3. Canary/kademeli açılış için: trafiğin bir kısmını (ör. %5-10) yeni versiyona yönlendirebiliyor musun (feature flag, load balancer weight, vb.)? Bu mekanizma yoksa, en azından "önce kendi/küçük beta grubu, sonra tam açılış" adımlarını manuel olarak nasıl uygulayacağını yaz.

`GO_LIVE_REPORT.md`'ye "## FAZ G-7 — Rollback ve Kademeli Çıkış" başlığıyla yaz.

---

## FAZ G-8 — Nihai GO/NO-GO Kararı

1. FAZ G-1'den G-7'ye kadar tüm testlerin PASS/FAIL/KISMEN durumunu tek tabloya topla: `Faz | Test | Karar | Blocker mı?`.
2. Blocker olarak işaretlenen her madde için: bu düzeltilmeden **GO verilemez**. Kaç blocker var, hangileri kısa sürede kapatılabilir, hangileri daha uzun çalışma gerektirir — ayır.
3. Blocker yoksa: "GO" kararını gerekçesiyle yaz (hangi kanıtlara dayandığını özetleyerek). Blocker varsa: "NO-GO — şu maddeler kapanmadan çıkılmamalı" diye net yaz, kararı yumuşatma.
4. İlk 24-48 saat için izlenecek metrik listesini (FAZ G-5'teki 4 alarm + G-1'deki benchmark Hit@K karşılaştırması) tek paragrafta özetle.

Sonunda `>>> GO-LIVE DOĞRULAMASI TAMAMLANDI — KARAR: GO / NO-GO` yaz ve dur.