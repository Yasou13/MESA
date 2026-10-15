# Başlangıç ve yayın politikası

UTC çalışma kimliği: 20261014T120040Z

Dal: `fix/mesa-core-comprehensive-retrieval-hardening`; SHA: `8ddd2dc04cf697fe237e7a49e391543c01182d83`.

Başlangıç kullanıcı değişiklikleri: silinmiş `PROFILE_A_REPAIR_JOURNAL.md`, izlenmeyen `MESA_v080_release_prompt.md`; commit dışındadır. İstenen uzun talimat adı bulunamadı; eşdeğer içerik mevcut dosyadan okundu.

Tarihsel kayıtlar SHA-256 başlangıç envanteri inventory_baseline.json içinde. Komşu depolar yazılmayacak. Yayın için docs/release.md gereğince aynı release commitinde CI, gerçek provider, 24 saat soak, DR, imzalı annotated tag ve OIDC artifact doğrulaması gerekir. Kapılar azaltılmayacak.

Son yerel etiket v0.7.1. `git ls-remote --symref origin HEAD refs/tags/v0.8.0*`: exit 128, github.com DNS çözümlenemedi. Uzak sürüm/politika NOT_VERIFIED. `python`: exit 127; sistem python3 ve mevcut .venv incelenecek.

## Ağ geri geldiğinde doğrulanan durum

GitHub connector ve sandbox dışı git okuması çalıştı. Default main54d11678b64f7801934a6cd265db2083981a9f54; son gerçek releasev0.7.1; v0.8.0 tag yok. İlk DNS hatası güncel auth engeli olarak sunulmaz. Yayın engeli mandatory evidence ve review eksikliğidir; 13/14 raporları.
