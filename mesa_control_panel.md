# MESA Control Panel — Tek Seferlik İnşa Promptu (Halüsinasyon Korumalı)

> Bu dosyanın tamamını agent'a tek seferde yapıştır. Kaynak plan `Mesa_Control_Panel.md`'dir — planı ayrıca eklemene gerek yok, agent onu okuyup kendi görev envanterini çıkaracak. Bu prompt bir **inşa** promptudur (onarım.md'deki gibi bir "bulgu listesi" değil); bu yüzden doğrulama, "hata düzeldi mi" değil **"iddia edilen şey gerçekten var mı ve çalışıyor mu"** sorusuna göre kurulmuştur.

---

## 0. DOLDURULACAK ALANLAR

```yaml
proje_adi: "MESA Control Panel"
kod_dizini: "[ör. /home/yasin/mesa]"
kaynak_plan: "Mesa_Control_Panel.md"
cikti_dosyasi: "BUILD_REPORT.md"
db_dosya_yolu: "[ör. mesa'nın kullandığı gerçek SQLite dosya yolu]"
test_komutu: "[ör. pytest tests/ -x]"
admin_api_erisimi: "[ör. lokal admin token / .env değişkeni]"
faz_bolme_esigi: 8          # bir aşamada bu sayıdan fazla somut görev varsa alt-faza böl
onay_noktasi_politikasi: "[ör. her Aşama sonunda dur / sadece Aşama 1 ve 3 sonunda dur]"
```

---

## GENEL KURALLAR

1. Bu prompt sana bir **mimari/inşa planı** verecek (`[kaynak_plan]`), hazır bir görev tablosu değil. İlk işin planı okuyup somut, doğrulanabilir bir görev envanteri çıkarmak (Adım 1).
2. Aşamaları (planın kendi "Aşama 1 → Aşama 5" sıralaması) sırayla işle. Aşama içi görev sırası bağımlılığa göre belirlenir (bkz. Adım 1.2). `[onay_noktasi_politikasi]`'na göre her aşama/alt-faz sonunda `>>> AŞAMA N TAMAMLANDI — X/Y görev inşa edildi, Z blocked — onay bekleniyor.` yaz ve dur.
3. **Hiçbir görev, HALÜSİNASYONDAN KORUNMA PROTOKOLÜ'ndeki kanıt zorunluluğu karşılanmadan "İNŞA EDİLDİ" sayılamaz.** Bu, bu promptun en kritik kuralıdır — aşağıdaki bölüm 3'ü atlama.
4. Plan metnindeki "current state" (mevcut durum) iddialarını **doğru kabul etme** — Adım 0'da doğrula. Plan yazıldığından beri kod değişmiş olabilir.
5. Plan bazı yerlerde tasarım tartışması/gerekçe içeriyor (ör. "8/10 → 9.3/10 puanım" gibi), bunlar talimat değildir — sadece somut şema/endpoint/tool/ekran tanımlarını göreve çevir.

---

## ADIM 0 — MEVCUT KOD DURUMUNUN DOĞRULANMASI (inşaya başlamadan önce)

Plan şu iddialarla açılıyor — inşaya başlamadan önce **her birini gerçek kodda doğrula**, doğru/yanlış olduğunu `[cikti_dosyasi]`'ye kaydet:

| Planın iddiası | Doğrulama yöntemi |
|---|---|
| MCP yalnızca stdio çalışıyor | `mesa_mcp/` içinde transport/server kodunu oku |
| Yalnızca 5 araç sunuluyor | Tool kayıt noktasını (`server.py` / tool registry) grep'le, gerçek sayıyı say |
| Doğrudan storage'a değil HTTP API'sine bağlanıyor | İstemci kodundaki bağlantı katmanını incele |
| Hâlâ V3 endpoint'leri kullanılıyor | `mesa_mcp/http_service.py` içindeki URL'leri grep'le |
| `actor_id`/`namespace`/`project` ortam değişkeninden sabit alınıyor | İlgili config/env okuma noktasını bul |
| Client bağlantısı/oturum/aktivite geçmişi/politika tutulmuyor | İlgili tablo/şema var mı diye DB şemasını (`[db_dosya_yolu]`) sorgula |

Herhangi bir iddia **yanlışsa** (plan yazıldığından beri kod değişmişse), bunu `[cikti_dosyasi]`'nin en başına "⚠️ Plan-Kod Çelişkisi" olarak yaz ve **kendi inisiyatifinle planı sessizce yeniden yorumlama** — çelişkiyi raporla, ilgili görevi buna göre uyarla, gerekiyorsa ilk onay noktasında kullanıcıya sor.

---

## ADIM 1 — GÖREV ENVANTERİNİN ÇIKARILMASI

### 1.1 — Envanter Tablosu
Planı baştan sona tara (şema tanımları, endpoint listeleri, tool tanımları, middleware zinciri adımları, dashboard ekranları, güvenlik kararları dahil — sadece "Uygulama aşamaları" bölümündeki madde işaretlerine güvenme, orası özet niteliğinde). Her somut, inşa edilebilir birimi bir satıra çevir:

| G-ID | Aşama | Tip | Açıklama | Konum (dosya/modül) | Kabul Kriteri |
|---|---|---|---|---|---|
| G-01 | 1 | Şema | `mcp_clients` tablosu | `mesa_mcp/` migration | Tablo gerçek DB'de `PRAGMA table_info` ile görülebiliyor |
| ... | | | | | |

Tip kategorileri: **Şema** (CREATE TABLE), **Endpoint** (REST route), **Tool** (MCP tool), **Middleware** (zincir adımı), **UI** (dashboard ekranı), **Politika/Config** (varsayılan kural, ayar).

### 1.2 — Bağımlılık Sırası
Plan bunu zaten büyük ölçüde belirtiyor (ör. Approval Queue → Aşama 1'de olmalı çünkü Aşama 3'teki Approvals ekranı ona bağımlı; V4 adapter → Aşama 2, ama middleware zinciri Aşama 1'de kurulmalı ki Aşama 2 üstüne binsin). Her görev için "Bağımlı: G-XX" notunu ekle. Aynı dosyaya/modüle dokunan görevleri (ör. `server.py`'ye hem client registry hem middleware entegrasyonu ekleniyor) art arda, aynı alt-fazda planla.

### 1.3 — Ölçeklendirme
Bir aşamada `[faz_bolme_esigi]`'den fazla görev varsa (örn. Aşama 1'in kendisi zaten client registry + connection registry + activity recorder + policy engine + approval queue + migrations + admin API + middleware entegrasyonu = 8 büyük birim içeriyor, bunların her biri kendi içinde alt-görevlere ayrılacak), aşamayı G-ID lokalitesine göre alt-fazlara böl (Aşama 1a, 1b...), her biri kendi onay noktasıyla bitsin.

Nihai envanteri ve aşama/alt-faz planını `[cikti_dosyasi]`'ye yaz, ilk onay noktasında göster, sonra inşaya geç.

---

## 3. HALÜSİNASYONDAN KORUNMA PROTOKOLÜ (en kritik bölüm)

Bu plan çok sayıda şema, endpoint, tool ve ekran tanımlıyor — bu ölçekte bir agent'ın "muhtemelen doğru yazdım" diyerek ilerlemesi en büyük risktir. Aşağıdaki kurallar **her görev için, istisnasız** uygulanır:

1. **"İnşa edildi" cümlesinden önce kendine sor:** *Bunu gerçekten çalıştırıp/sorgulayıp gördüm mü, yoksa kod mantıken doğru göründüğü için mi varsayıyorum?* Cevap ikinciyse, DURMA — önce çalıştır.
2. **Yazdığını tekrar oku.** Bir dosyaya kod yazdıktan hemen sonra, ayrı bir adımda o dosyayı tekrar aç (view) ve yazdığının gerçekten orada olduğunu, syntax hatasız olduğunu teyit et. "Yazma aracı başarı döndürdü" tek başına yeterli kanıt değildir.
3. **Şema iddiaları:** "Tablo oluşturuldu" demeden önce gerçek DB bağlantısı üzerinden `PRAGMA table_info(tablo_adi)` (veya eşdeğeri) çalıştır, çıktıyı `[cikti_dosyasi]`'ye yapıştır. Migration dosyasının var olması yeterli değil — migration'ın gerçekten uygulandığını göster.
4. **Endpoint iddiaları:** "Endpoint eklendi" demeden önce gerçek bir HTTP isteği (curl/httpx) at, status code + response body'yi kanıt olarak yapıştır. Route tanımının kodda görünmesi yeterli değil.
5. **Tool iddiaları:** "Tool eklendi" demeden önce MCP `tools/list` çıktısında gerçekten göründüğünü VE en az bir örnek çağrının gerçek sonucunu (başarı veya beklenen hata) göster.
6. **UI/dashboard iddiaları:** "Ekran eklendi" demeden önce ilgili route/component'in gerçekten render olduğunu (headless test, screenshot, veya en azından derlemenin/lint'in hatasız geçtiğini) göster.
7. **Sayı/liste iddialarını asla ezbere verme.** "5 araç var" gibi bir sayı söyleyeceksen, o sayıyı üreten komutu (grep/count) çalıştır ve çıktısını göster.
8. **Belirsizlik durumunda uydurma.** Plan bir noktada belirsizse (ör. "SQL'de JSON saklanabilir veya normalize edilmiş politika kuralları kullanılabilir" gibi iki seçenek sunuluyor), agent kendi başına "daha iyi" olanı seçip bunu plan gereğiymiş gibi sunmaz — seçimi ve gerekçesini açıkça `[cikti_dosyasi]`'ye yazar (plan burada normalize modeli öneriyor, o hâlde varsayılan odur, ama agent bunu netçe belirtmelidir).
9. **Aşama kapanışında çapraz kontrol.** Bir aşama "tamamlandı" denmeden önce, o aşamadaki **her** G-ID'nin kabul kriteri tek tek listelenip kanıtla eşleştirilmeli — özet geçilerek "hepsi tamam" denemez.
10. **Regresyon iddiaları da kanıt gerektirir** (bkz. Bölüm 4, Adım 2) — "mevcut V3 araçlar hâlâ çalışıyor" demeden önce onları da gerçekten çağır.

---

## 4. İKİLİ DOĞRULAMA PROTOKOLÜ (her görev için)

### Adım 1 — Gerçekten İnşa Edildi mi?
- Kabul kriterini (Adım 1.1'de tanımlanan) çalıştır, ham çıktıyı kaydet.
- Kanıt: test PASS / gerçek sorgu çıktısı / gerçek HTTP yanıtı / gerçek tool çağrısı sonucu. Yorum/varsayım kabul edilmez.

### Adım 2 — Bu Görev Mevcut Sistemi Bozdu mu?
- Değiştirilen dosya/modülü kullanan tüm çağıranları grep ile bul.
- `[test_komutu]` ile mevcut test suite'ini önce/sonra karşılaştır.
- Özellikle: eski V3 araçlar/endpoint'ler hâlâ çalışıyor mu (plan "V3 compatibility olarak kalabilir" diyor — bunu bozmamak zorunlu)? Mevcut `MemoryDAO` şişirilmedi mi (plan bunu açıkça yasaklıyor — Control Panel mantığı `ControlRepository`/`PolicyRepository`/`ActivityRepository`/`ApprovalRepository`/`ConnectionRepository` gibi ayrı katmanlarda mı kaldı, `MemoryDAO`'ya sızmadı mı — grep ile teyit et)?
- Regresyon bulunursa: yeni satır olarak envanterin sonuna ekle, düzelt, Adım 1-2'yi tekrarla.

### `[cikti_dosyasi]` Şablonu (her görev için)

```
### [G-ID] Başlık
- **Aşama / Alt-faz:** ...
- **Tip:** Şema / Endpoint / Tool / Middleware / UI / Politika
- **Konum:** dosya:satır
- **Ne inşa edildi:** (özet + diff referansı)
- **Adım 1 (Kanıt):** [gerçek komut çıktısı / test sonucu] — ✅ Doğrulandı / ❌ Eksik
- **Adım 2 (Regresyon):** [kontrol edilenler] — ✅ Yok / ⚠️ Yeni Bulgu: ...
- **Durum:** İNŞA EDİLDİ / KISMEN / BLOCKED (neden)
```

---

## FAZ YAPISI (planın kendi aşamaları + ölçeklendirme)

- **Aşama 1 — Control plane çekirdeği:** client registry, connection registry, activity recorder, policy engine, approval queue, migrations, admin API, mevcut stdio server'a middleware entegrasyonu. `[faz_bolme_esigi]` aşıldığı için muhtemelen 1a/1b/1c'ye bölünecek (Adım 1.3).
- **Aşama 2 — V4-native MCP:** V4 service adapter, principal/dataset binding, mutation/pipeline ID takibi, `remember/recall/improve/forget`, replay/rollback bağlantıları. **Bağımlı:** Aşama 1'deki middleware zinciri.
- **Aşama 3 — Web dashboard MVP:** Overview, Connections, Activity, Approvals, Memories, Settings. **Bağımlı:** Aşama 1 (activity/approval verisi olmadan dashboard boş olur).
- **Aşama 4 — Merkezi HTTP MCP Gateway:** çoklu istemci, connection lifecycle, credential-based identity, heartbeat, revocation. Stdio kaldırılmaz, `stdio → local bridge → central gateway` veya standalone korunur — bunu regresyon kontrolünde özellikle doğrula.
- **Aşama 5 — Gelişmiş operasyon:** graph explorer, queue yönetimi, policy simulator, anomaly detection, retention, export/import, latency tracing, alerts.

---

## NİHAİ KABUL KONTROLÜ (planın kendi MVP tanımı)

Aşama 3 sonunda, planın "MVP'de kesin olması gerekenler" listesindeki 10 maddeyi ayrı bir kapanış kontrolü olarak çalıştır — her biri için Bölüm 3'teki kanıt kurallarını uygula:

```
1. Overview
2. Clients and Connections
3. Activity
4. Pending Approvals
5. Memory Explorer
6. Global and client policies
7. Manual enable/disable controls
8. V4 project/dataset bindings
9. Read/write/delete approval modes
10. Safe audit trail
```

Planın "İlk sürümde olmayanlar" listesindeki maddelerin (tam graph visualization, gelişmiş analytics, anomaly detection, ayrıntılı cost accounting, karmaşık workflow designer) bu aşamada **kasıtlı olarak** yapılmadığını da not et — eksik değil, kapsam dışı.

---

## KAPANIŞ

1. Tüm görevleri özet tabloya topla: `G-ID | Aşama | Durum | Adım 1 | Adım 2 | Yeni Bulunan Sorun`.
2. `[test_komutu]` ile tüm test suite'ini uçtan uca bir kez daha çalıştır, sonucu kaydet.
3. Adım 0'daki "Plan-Kod Çelişkisi" notlarının hepsinin çözülmüş/ele alınmış olduğunu teyit et.
4. Tek paragraflık nihai özet: kaç görevden kaçı tam inşa edildi, kaçı kısmen, kaçı blocked, hangi aşamalar tamamlandı.
5. `>>> MESA CONTROL PANEL İNŞA SÜRECİ İŞLENDİ` yaz ve dur.