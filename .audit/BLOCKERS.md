# Production Blocker’ları

Yalnızca production’a çıkışı engelleyen doğrulanmış sorunlar burada tutulur. Kanıtı yetersiz konular blocker olarak işaretlenmez.

| ID | Başlık | Kanıt durumu | Etki | Gerekli çözüm | Sahip / bağımlılık | Çıkış kriteri | Durum |
|---|---|---|---|---|---|---|---|
| — | Doğrulanmış keşif blocker’ı yok | — | — | — | — | — | Açık blocker yok |

## Açık keşif soruları (blocker değildir)

| ID | Konu | Somut kanıt | Sonraki doğrulama | Durum |
|---|---|---|---|---|
| Q-001 | İki API başlatma yolu arasındaki davranış eşitliği | Docker `mesa_memory.api.server:app` kullanırken `scripts/run_server.py` ayrıca FastAPI app ve `main()` tanımlar | Faz 1’de build/runtime baseline ile karşılaştır | Açık |
| Q-002 | Storage yolu ile Compose volume eşleşmesi | `MESA_STORAGE_PATH` ve Docker Compose’da ayrı SQLite/LanceDB/Kuzu mount yolları tanımlı | Faz 1’de yalnızca yapılandırma çözümlemesi, sonra runtime kanıtı | Açık |
| Q-003 | Pre-push hook etkinliği | `.githooks/pre-push` mevcut; Git `core.hooksPath` ayarı incelenmedi | Operasyon fazında Git ayarı/CI ilişkisini doğrula | Açık |
| Q-004 | Gerçek environment değerleri | `.env.example` ve koddan değişken adları çıkarıldı; gerçek `.env` değerleri güvenlik kuralı gereği okunmadı | Yetkili ve maskeli yapılandırma incelemesi gerekiyorsa kullanıcı onayı al | Açık |
