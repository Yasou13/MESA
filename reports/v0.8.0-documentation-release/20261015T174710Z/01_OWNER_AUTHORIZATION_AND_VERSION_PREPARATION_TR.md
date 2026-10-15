# v0.8.0 açık owner onayı ve sürüm hazırlığı

Tarih:2026-10-15UTC. Önceki PR108 merge46da203bf382a4ee209642590a161d0f902c345e. Mevcut authorized repair branch korunur. Journal silmesi ve talimat dosyası stage/commit dışındadır. Önceki audit run raporları ve cross-repo kanıtlar değişmez.

İnsan kullanıcının açık talimatı: “bişi olmaz sen releasesi yayınla ben onaylıyorum”. Önceki yanıtta provider/24h soak, main CI, metadata, imza/attestation ve method/format borcu bildirilmişti. Bu yeni onay v0.8.0 source release için bilinen eksikliklerin yayın istisnasıdır; production GO, passed soak, gerçek model kanıtı veya ProfileB sertifikasyonu değildir. Genel policy/test/security kontrolleri kaldırılmadı. docs/release.md yalnız bu tarihli sürüm istisnasını kaydeder.

GPG --list-secret-keys --with-colons exit0, secret key count0. İmzalama anahtarı üretilmedi veya güven uydurulmadı. Tag annotated fakat unsigned olacak; signed-tag preflight PASS iddiası verilmeyecek. Pipeline'daki metadata/build/SBOM/OIDC ve CI kontrolleri aynen korunur ve sonuçları ayrıca okunacak.

Sürüm:pyproject/uv.lock0.8.0; SDK User-Agent, dashboard, Compose, OpenAPI info.version ve maintained giriş belgeleri eşitlendi. OpenAPI diğer47path/sözleşme birebir aynı. Eval çıktılarındaki güncel sürüm etiketleri düzeltildi; eski fixture/rapor/benchmark kanıtları aynen kaldı. CHANGELOG Unreleased içeriği tarihli0.8.0 bölümüne taşındı;0.7.1 ve önceki release bölümleri değişmedi.

uv lock --offline ilk resolver denemesi istenmeyen transitif sürüm önerdi; hiç kurulmadan geri alındı. Eski lockfile'da yalnız mesa-memory version değişti; uv lock --check --offline exit0,251paket. Editable metadata izolasyonsuz backend yokluğu nedeniyle ilk denemede kurulmadı; pyproject'in tanımlı izole backend'iyle kurulum exit0,0.8.0. Bunlar PASS diye saklanmadı; düzeltme sonrası check sonuçları local-checks.json'dadır.

Yerel doğrulama: docs44Markdown/3HTML/102link0hata; Ruff ve metadata preflight0; küçük test grubu, dashboard build/lint0. Peak459.4MiB;1.8GiB RSS/3GiB MemAvailable koruması tetiklenmedi. Full suite ve modeller yerelde tekrar başlatılmadı; yeni sürüm PR'ı tam GitHub Actions'tan geçecek.
