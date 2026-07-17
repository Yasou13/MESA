# MESA — Tüm Bulguların Kapatılması: Master Düzeltme Promptu

**Kaynak:** `REPORT.md` + `REPORT_UNDOCUMENTED.md` + `REPORT_CLOSING.md` — toplam **49 bulgu** (3 raporun kendi tablolarında + `REPORT.md` 9.2'de tabloya hiç girmemiş 3 ek kritik bulgu dahil).

**Kullanım:** Bu dosyanın tamamını agent'a yapıştır. Fazlar sırayla işlenecek, bir faz bitmeden sonrakine geçilmeyecek. Sonuçlar tek bir `FIX_REPORT.md` dosyasına yazılacak.

---

## GENEL TALİMATLAR

1. Fazları sırayla işle. Her fazın sonunda `>>> FAZ R-N TAMAMLANDI — X/Y bulgu kapatıldı, Z bulgu blocked/ertelendi — onay bekleniyor.` yaz ve dur.
2. Her bulgu için **ZORUNLU İKİLİ DOĞRULAMA PROTOKOLÜ** uygulanacak (aşağıda detaylı). Bu protokol atlanmadan bir bulgu "kapatıldı" sayılamaz.
3. Aynı kök nedene sahip bulgular **tek düzeltme** ile kapatılacak (aşağıda "Dedup Grubu" olarak işaretlendi) — ama her ikisi de ayrı ayrı doğrulanacak.
4. Bazı bulguların orijinal raporda dosya/satır numarası **yoktu** (sadece özet bölümünde geçiyordu) — bu bulgular için ilk adım **konumu koda bakarak tespit etmek**tir; konum bulunamazsa "Konum Bulunamadı — Faz N'e tekrar bakılmalı" diye işaretle, atlama.
5. Sadece "dokümantasyon eklenmesi" gerektiren pozitif/no-action bulgular (aşağıda işaretli) FAZ R-5'te tek seferde toplu işlenir — bunlara ikili doğrulama protokolü uygulanmaz (kod değişikliği yok, regresyon riski yok).

---

## ZORUNLU İKİLİ DOĞRULAMA PROTOKOLÜ (Her kod-düzeltmesi için)

Bir bulguyu "ÇÖZÜLDÜ" olarak işaretlemeden önce agent şu 2 adımı **sırayla, atlamadan** tamamlamalı ve `FIX_REPORT.md`'ye kanıtla birlikte yazmalı:

### Adım 1 — Orijinal Hata Gerçekten Çözüldü mü? (Pozitif Doğrulama)

- Hatanın orijinal raporda tarif edilen **tam senaryosunu** yeniden oluştur (mümkünse otomatik bir test/repro script yaz).
- Fix öncesi bu senaryo başarısız olduğunu (veya hatayı tetiklediğini) göster — fix'ten önce çalıştırıp kanıtla.
- Fix'i uygula.
- Aynı senaryoyu **tekrar** çalıştır, artık hatanın oluşmadığını somut çıktıyla (test PASS, log, assert sonucu) göster.
- Sadece "kod değişti, mantıken düzelmiş olmalı" demek YETERSİZDİR — çalıştırılmış bir kanıt zorunlu.

### Adım 2 — Bu Düzeltme Yeni Bir Hataya Yol Açtı mı? (Regresyon Kontrolü)

- Değiştirilen dosya/fonksiyonu kullanan **tüm çağıranları** (call sites) bul (grep/import taraması).
- İlgili mevcut test suite'ini (varsa `tests/` altındaki dosyayı) çalıştır — önce ve sonra karşılaştır, yeni FAIL var mı kontrol et.
- Eğer o modül için test yoksa, en az şu 3 riski elle kontrol et: (a) fonksiyonun imzası/dönüş tipi değişti mi ve bunu kullanan başka yer var mı, (b) performans karakteristiği kötüleşti mi (ör. senkron hale gelen bir async çağrı), (c) aynı düzeltme başka bir agent_id/tenant/veri yolunu etkiliyor mu (özellikle RLS ile ilgili fixlerde).
- Eğer düzeltme bir "dedup grubu"nun parçasıysa, grubun **diğer** çağrı noktasını da regresyon açısından kontrol et.
- Bir regresyon bulunursa: yeni bulgu olarak `FIX_REPORT.md`'ye ekle (aynı şablonla), düzeltmeyi ayarla, Adım 1 ve 2'yi baştan çalıştır.

### FIX_REPORT.md Şablonu (her bulgu için)

```
### [R-ID] Başlık
- **Kaynak Rapor / Orijinal ID:** ...
- **Önem:** Kritik / Yüksek / Orta / Düşük
- **Konum:** dosya:satır
- **Uygulanan Fix:** (kısa özet + diff/patch referansı)
- **Adım 1 Sonucu (Orijinal Hata Çözüldü mü?):** [KANIT: test adı/çıktısı] — ✅ Doğrulandı / ❌ Hâlâ Mevcut
- **Adım 2 Sonucu (Regresyon Var mı?):** [Kontrol edilen call site'lar / testler] — ✅ Regresyon Yok / ⚠️ Yeni Bulgu: [açıklama]
- **Durum:** ÇÖZÜLDÜ / KISMEN ÇÖZÜLDÜ / BLOCKED (neden)
```

---

## KONSOLİDE BULGU ENVANTERİ (Dedup Gruplarıyla)

|R-ID|Kaynak (Orijinal ID)|Önem|Konum|Not|
|---|---|---|---|---|
|R-01|REPORT (3-1)|Kritik|`vector_engine.py`|Vektör silmede `agent_id` yok (IDOR)|
|R-02|REPORT (4-2)|Kritik|`maintenance.py`|Soft-delete ASCII tarih kıyas hatası|
|R-03|REPORT (1-1) + UNDOC (U-1.2)|Kritik|`dao.py` + `entity_consolidation_worker.py`|**Dedup Grubu A** — Dual-Write Saga erken commit (2 çağrı noktası)|
|R-04|REPORT (3-2)|Kritik|`server.py`|`/v3/health` korumasız|
|R-05|REPORT (3-3)|Kritik|`rbac.py`|`sanitize_cmb_content` hiç çağrılmıyor|
|R-06|REPORT (EK, 9.2#1)|Kritik|`server.py` (konum teyit edilecek)|Retention worker devre dışı (comment-out)|
|R-07|REPORT (EK, 9.2#3)|Kritik|KùzuDB provider (konum teyit edilecek)|Read-only sorgularda connection leak|
|R-08|REPORT (EK, 9.2#4)|Kritik|RLS sorguları (konum teyit edilecek)|`LIKE` tabanlı agent eşleştirme → strict `==` olmalı|
|R-09|CLOSING (K-1.1)|Kritik|`mesa_client/client.py:127`|SDK `Bearer` header, backend `X-API-Key` bekliyor|
|R-10|CLOSING (K-2.1)|Kritik|`mesa_mcp/server.py:30`|`agent_id` LLM tool argümanı → prompt injection ile tenant spoofing|
|R-11|UNDOC (U-6.1)|Kritik|`scripts/run_demo_rag.py`|`/v3/demo/chat` auth'suz, direct-write|
|R-12|REPORT (2-1)|Yüksek|`vector_engine.py`|ThreadPoolExecutor kapanış sızıntısı|
|R-13|REPORT (4-3)|Yüksek|`reranker.py`|CrossEncoder unbounded thread pool (OOM)|
|R-14|REPORT (6-2)|Yüksek|`llm_judge.py`|Benchmark judge fallback sessiz doğrulama|
|R-15|REPORT (7-1) + UNDOC (U-1.1)|Yüksek|`ingestion_worker.py:790-791`|**Dedup Grubu B (aynı bug, tek konum)** — N+1 embedding|
|R-16|UNDOC (U-2.1)|Yüksek|`mesa_evals/dataset.py` vs `mesa-benchmark/datasets/`|İki ayrı golden dataset şeması çatışması|
|R-17|UNDOC (U-4.2)|Yüksek|`tracer.py` (yok) / `routing_telemetry`|LLM tracing (LangFuse/LangSmith) entegrasyonu eksik|
|R-18|CLOSING (K-1.2)|Yüksek|`mesa_client/client.py:81`|Idempotent olmayan POST'ta güvensiz retry|
|R-19|CLOSING (K-1.3) + K-2.2|Yüksek|`mesa_client/langchain.py:50` + `mesa_mcp/server.py:153`|**Dedup Grubu C** — `SearchResultItem`'da `content_payload` eksik (payload kaybı)|
|R-20|REPORT (1-3)|Orta|`router.py`|`_hydrate_embeddings` boşa düşüyor|
|R-21|REPORT (3-4)|Orta|Tüm sistem|Prompt injection tasarım riski (genel)|
|R-22|REPORT (4-4)|Orta|`hybrid.py`|Sessiz bypass (fail-open)|
|R-23|REPORT (5-1)|Orta|`loop.py`|DLQ yeniden işleme yok|
|R-24|REPORT (6-1)|Orta|`tests/`|WAL recovery senaryoları test edilmemiş|
|R-25|REPORT (7-2)|Orta|`hybrid.py`|FTS sorgusu senkron bekletiliyor|
|R-26|UNDOC (U-2.2)|Orta|`mesa-benchmark/.../llm_judge.py:93`|Judge self-consistency/ensemble yok|
|R-27|UNDOC (U-3.2)|Orta|`legal_generator.py` + `router.py:268`|Legal mode maliyet/latency uyarısı yok, `legal_audit.py` gatekeeper'a bağlı değil|
|R-28|UNDOC (U-5.1)|Orta|`scripts/` (doküman temizliği)|Hayalet script referansları (silinen dosyalar hâlâ dokümanlarda)|
|R-29|CLOSING (K-1.4)|Orta|`mesa_client/client.py`|SDK/API versiyon uyumluluk kontrolü yok|
|R-30|CLOSING (K-3.3)|Orta|`test_pagerank_coverage.py` vb.|`asyncio.sleep` tabanlı flaky test riski|
|R-31|CLOSING (K-4.1)|Orta|`install.sh:68`|`curl \| sh` güvensiz kurulum|
|R-32|REPORT (1-2)|Düşük|`ARCHITECTURE.md`|RBAC flow param tipi tutarsızlığı|
|R-33|REPORT (2-2)|Düşük|`schemas.py`|Eski `StorageFacade` fonksiyonları (ölü kod)|
|R-34|REPORT (5-2)|Düşük|`server.py`|`/health/init` yüzeysel kontrol|
|R-35|REPORT (7-3)|Düşük|`maintenance.py`|`VACUUM` saatleri hardcoded|
|R-36|REPORT (8-1)|Düşük|`api-reference.md`|Eski `StorageFacade` doküman referansları|
|R-37|REPORT (8-2)|Düşük|`pyproject.toml`|Versiyon uyuşmazlığı (`0.5.2` vs `0.6.0`)|

**FAZ R-5'e (dokümantasyon-only / pozitif bulgu, kod değişikliği gerektirmeyen) aktarılan bulgular:**

|R-ID|Kaynak|Önem|Not|
|---|---|---|---|
|R-38|UNDOC (U-1.3)|Düşük|PageRank quarantine — pozitif, sadece doküman eki|
|R-39|UNDOC (U-2.3)|Düşük|Rakip client context-ID eşleşmesi — pozitif, işlem yok|
|R-40|UNDOC (U-3.1)|Düşük|Soak test CI'a bağlı değil — nightly pipeline'a eklenmeli (küçük DevOps işi)|
|R-41|UNDOC (U-3.3)|Düşük|Load test — pozitif, sadece doküman eki|
|R-42|UNDOC (U-4.1)|Düşük|Prometheus/JSON logging belgesiz — sadece doküman eki|
|R-43|CLOSING (K-2.3)|Düşük|MCP API üzerinden geçiyor — pozitif, işlem yok|
|R-44|CLOSING (K-3.1)|Düşük|802 testlik kapsamlı suite — pozitif, işlem yok|
|R-45|CLOSING (K-3.2)|Düşük|Gerçekçi prod-like fixture — pozitif, işlem yok|
|R-46|CLOSING (K-4.2)|Düşük|Docker non-root + CI secrets hijyeni — pozitif, işlem yok|
|R-47|Bu prompt'un kendi tespiti|Düşük|`REPORT_CLOSING.md` kendi nihai özetinde Kritik/Yüksek sayısını yanlış toplamış (9 Kritik/10 Yüksek yerine "8 Kritik/6 Yüksek" yazmış) — raporun kendisi düzeltilmeli|
|R-48|Bu prompt'un kendi tespiti|Düşük|`REPORT.md` 9.2'deki 3 bulgu (R-06, R-07, R-08) ana tabloya hiç eklenmemiş — `REPORT.md` tablosu güncellenmeli|
|R-49|(Rezerve)|—|Faz R-1..R-4 sırasında yeni bulunan/regresyon bulgular buraya numaralanarak eklenecek|

---

## FAZ R-1 — Kritik Bulguların Kapatılması (R-01 → R-11)

Sırayla, her biri için ikili doğrulama protokolünü uygula. Özellikle dikkat:

- **R-03 (Dedup Grubu A):** Merkezi bir `_atomic_saga_commit()` helper'ı yazıp hem `dao.py`'deki `update`'i hem `entity_consolidation_worker.py`'deki `update_entity_description`'ı buna yönlendir. Regresyon kontrolünde **her iki çağrı noktasını da** ayrı ayrı test et. Ayrıca kod tabanında aynı "commit-then-upsert sırası ters" pattern'inin başka bir üçüncü yerde olup olmadığını grep ile tara (`await db.commit()` civarındaki tüm dual-write noktaları).
- **R-06/R-07/R-08:** Önce konumu kesinleştir (kod içinde ara), sonra fix uygula. Konum bulunamazsa bunu açıkça belirt ve bir sonraki fazı bekletme — ayrı not olarak işaretleyip devam et.
- **R-10 (MCP tenant spoofing):** Fix sonrası regresyon kontrolünde, `MESA_AGENT_ID` env değişkenine geçişin **mevcut çok-agent'lı MCP kurulumlarını** (aynı MCP sunucusu birden fazla agent'a hizmet ediyorsa) kırıp kırmadığını özellikle kontrol et — bu praktik bir davranış değişikliği, sadece güvenlik fixi değil.
- **R-11 (Demo auth bypass):** Fix'in demo'nun kendi işlevselliğini (gerçek kullanıcı akışını) bozmadığını da doğrula — `Depends(get_api_key)` eklendikten sonra demo arayüzünün API key'i doğru şekilde gönderip göndermediğini uçtan uca test et.

`FIX_REPORT.md`'ye "## FAZ R-1 — Kritik" başlığıyla yaz.

---

## FAZ R-2 — Yüksek Öncelikli Bulguların Kapatılması (R-12 → R-19)

- **R-15 (N+1 embedding):** Adım 1 doğrulamasında gerçek bir ingestion senaryosu kur (20 triplet üreten bir metin), fix öncesi/sonrası **API çağrı sayısını ölç** (log/mock sayacı ile), sadece "kod artık batch kullanıyor" demekle yetinme. Regresyon: batch embedding çağrısının kısmi hata durumunda (ör. 20 triplet'ten 3'ü embedding servisinden hata dönerse) tüm batch'i mi düşürüyor yoksa kısmi başarı senaryosu doğru yönetiliyor mu kontrol et — batch'e geçiş bazen "tek hata → tüm batch fail" riskini getirir.
- **R-19 (Dedup Grubu C):** `SearchResultItem` şemasına `content_payload` eklendikten sonra, bunu tüketen **her iki** yeri (`langchain.py` VE `mesa_mcp/server.py`) ayrı ayrı test et. Ayrıca şema değişikliğinin (yeni zorunlu/opsiyonel alan) mevcut API tüketicilerinde (varsa başka bir yerde `SearchResultItem` parse eden kod) regresyona yol açıp açmadığını grep ile tara.
- **R-17 (LLM tracing):** Bu bir "eksik özellik ekleme" bulgusu — Adım 1 doğrulaması burada "trace verisi gerçekten LangFuse/LangSmith'e ulaşıyor mu" şeklinde olmalı (canlı bir dashboard/log kontrolü). Regresyon: callback eklenmesinin LiteLLM çağrı latency'sini gözle görülür şekilde artırıp artırmadığını ölç.

`FIX_REPORT.md`'ye "## FAZ R-2 — Yüksek" başlığıyla yaz.

---

## FAZ R-3 — Orta Öncelikli Bulguların Kapatılması (R-20 → R-31)

Her biri için standart ikili doğrulama uygula. Özellikle:

- **R-25 (FTS senkron bekletme):** Fix (FTS'i `asyncio.gather`'a alma) sonrası regresyon kontrolünde, üç kaynaktan (vector/graph/FTS) gelen sonuçların **birleştirme sırasının** (Alpha reranking) değişmediğini doğrula — paralelleştirme bazen sonuç sıralamasını farklı şekilde etkileyebilir.
- **R-30 (flaky test):** Fix'in (`sleep` → poll/retry) CI'da gerçekten daha az flaky olduğunu görmek için değiştirilen testi CI'da art arda birkaç kez çalıştır (tek seferlik yeşil yetmez).

`FIX_REPORT.md`'ye "## FAZ R-3 — Orta" başlığıyla yaz.

---

## FAZ R-4 — Düşük Öncelikli Kod Bulgularının Kapatılması (R-32 → R-37)

Bunlar çoğunlukla dokümantasyon/ölü kod/config temizliği — yine de her kod değişikliği (ör. R-33 ölü kod silme, R-35 config'e taşıma) için ikili doğrulamayı uygula, özellikle "ölü kod silme" fixlerinde regresyon kontrolü kritik: silinen fonksiyonun gerçekten hiçbir yerde import edilmediğini grep ile teyit et.

`FIX_REPORT.md`'ye "## FAZ R-4 — Düşük" başlığıyla yaz.

---

## FAZ R-5 — Dokümantasyon-Only ve Pozitif Bulgular (R-38 → R-48)

Kod değişikliği yok; ikili doğrulama protokolü uygulanmaz. Tek seferde:

1. R-38, R-39, R-41, R-42, R-43, R-44, R-45, R-46: `ARCHITECTURE.md`'ye ilgili bölümleri ekle (önceki raporlarda taslakları zaten var — onları kullan).
2. R-40: `soak_test.py`'yi nightly CI pipeline'ına ekle (bu küçük bir DevOps değişikliği — CI dosyasını değiştirdiğin için buna da hafif bir regresyon kontrolü uygula: CI'ın diğer job'larını bozmadığını doğrula).
3. R-47: `REPORT_CLOSING.md`'deki nihai özet paragrafını doğru sayılarla (9 Kritik, 10 Yüksek) güncelle.
4. R-48: `REPORT.md`'nin ana bulgu tablosuna R-06/R-07/R-08'i (kendi orijinal ID'leriyle, ör. 4-5, 3-5, 9-1) ekle.

`FIX_REPORT.md`'ye "## FAZ R-5 — Dokümantasyon ve Pozitif Bulgular" başlığıyla yaz.

---

## FAZ R-6 — Konsolide Kapanış ve Nihai Sağlık Kontrolü

1. `FIX_REPORT.md`'deki tüm bulguları özet tabloya topla: `R-ID | Durum (ÇÖZÜLDÜ/KISMEN/BLOCKED) | Adım 1 | Adım 2 | Yeni Bulunan Regresyon Var mı`.
2. Tüm düzeltmeler tamamlandıktan sonra **tam test suite'ini** (`tests/` altındaki tüm 802 test) bir kez daha uçtan uca çalıştır — tek tek modül testleri değil, bütün suite. Sonucu kaydet.
3. Eğer R-49'a (rezerve) süreç boyunca yeni bulgu eklendiyse, bunları da aynı ikili doğrulama protokolüyle kapat, sonra bu fazı tekrarla.
4. Son olarak: "Kaç bulgudan kaçı tam çözüldü, kaçı kısmen, kaçı blocked" şeklinde tek paragraflık bir nihai durum özeti yaz.

Sonunda `>>> TÜM BULGULAR İŞLENDİ` yaz ve dur.