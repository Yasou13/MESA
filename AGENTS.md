# MESA Çalışma Kuralları

Bu repository baştan sona incelenecek ve production ortamına hazırlanacaktır. Bu dosya, Codex’in repository’de yapacağı tüm analiz ve düzeltme çalışmalarının ana talimatıdır.

## Kapsam

Çalışma aşağıdaki başlıkları kapsar:

- Repository envanteri ve gerçek mimarinin çıkarılması
- Dokümantasyonun kodla karşılaştırılması
- Modüller arası bağımlılıklar, API’ler ve veri akışları
- İş mantığı, kod kalitesi, debugging ve hata yönetimi
- Veri bütünlüğü, transaction davranışı ve eşzamanlılık
- Worker, background job, queue, authentication ve authorization
- Multi-tenant veya agent izolasyonu ve güvenlik
- Test kapsamı, performans, migration, backup ve restore
- Docker, CI/CD, logging, metrics, monitoring, deployment ve rollback
- Production-readiness kararı

## Temel çalışma ilkesi

Çalışma her konu için aşağıdaki sırayla ilerler:

1. Keşfet.
2. Kanıtla.
3. Tekrar üret.
4. Kök nedeni belirle.
5. Regresyon testi yaz.
6. Minimum güvenli düzeltmeyi uygula.
7. Dar kapsamlı testi çalıştır.
8. İlgili test paketini çalıştır.
9. Regresyon kontrolü yap.
10. Dokümantasyonu güncelle.

Bir problem yalnızca kod okunarak kesin kabul edilmez. Mümkün olduğunda çalışma zamanı davranışı ve testle doğrulanır.

## Faz sistemi

Fazlar sırayla yürütülür; bir faz tamamlanmadan sonraki faza geçilmez.

1. Faz 0 — Repo keşfi ve kapsam doğrulama
2. Faz 1 — Kurulum, build ve çalışma baseline’ı
3. Faz 2 — Mimari ve bileşen ilişkileri
4. Faz 3 — Kritik veri akışları
5. Faz 4 — Modül bazlı kod ve iş mantığı analizi
6. Faz 5 — Güvenlik ve izolasyon
7. Faz 6 — Veri bütünlüğü ve concurrency
8. Faz 7 — Worker, queue ve background işlemleri
9. Faz 8 — Test sistemi ve test boşlukları
10. Faz 9 — Debugging ve kontrollü düzeltmeler
11. Faz 10 — Performans ve ölçeklenebilirlik
12. Faz 11 — Migration, backup ve restore
13. Faz 12 — Docker, CI/CD ve operasyon
14. Faz 13 — Staging ve deployment provası
15. Faz 14 — Production-readiness değerlendirmesi

Her faz sonunda `.audit/` altında şunlar raporlanır: yapılan işlemler, kanıtlar, sorunlar, açık belirsizlikler, değiştirilen dosyalar, çalıştırılan testler, çıkış kriterleri ve fazın tamamlanma durumu. Ardından kullanıcı onayı beklenir.

## Başlangıç ve Git kuralları

Her çalışma başlangıcında aşağıdakiler kontrol edilir:

- `git status`
- Aktif branch
- Commit hash
- Mevcut kullanıcı değişiklikleri

Kullanıcının mevcut değişiklikleri korunur. İlgisiz dosyalar değiştirilmez; büyük ve ilgisiz değişiklikler tek patch içinde birleştirilmez. Codex kendiliğinden commit veya push oluşturmaz. `main`, `master` veya production branch üzerinde uygulama kodu değiştirilmez.

## Güvenlik kuralları

Kullanıcı açıkça onaylamadan aşağıdakiler yapılmaz:

- Dosya veya klasör silmek; `rm -rf`, `git reset --hard`, `git clean` çalıştırmak
- Force push, commit veya push yapmak
- Gerçek production ortamına bağlanmak veya production veritabanında işlem yapmak
- Migration çalıştırmak; veritabanını ya da Docker volume’ünü silmek veya sıfırlamak; Docker prune çalıştırmak
- Sistem genelinde paket kurmak veya `sudo` kullanmak
- Dependency’leri topluca yükseltmek ya da lock dosyasını sebepsiz değiştirmek
- `.env` içindeki gerçek secret değerlerini okumak, yazmak veya raporlamak
- İnternetten indirilen scripti doğrudan çalıştırmak; `curl | bash` benzeri komutlar kullanmak

API key, token, parola, cookie, private key, bağlantı adresi veya kişisel veri hiçbir rapora açık biçimde yazılmaz. Secret tespit edilirse yalnızca dosya konumu ve secret türü, değer gösterilmeden kaydedilir.

## Kanıt ve bulgu kuralları

Her teknik bulgu şu alanları içerir:

- Bulgu ID, kısa başlık, durum, önem, öncelik ve kategori
- Release blocker olup olmadığı
- Dosya yolu ile satır veya sembol referansı
- Beklenen ve gerçek davranış
- Somut kanıt ve tekrar üretme adımları
- Etki, kök neden, önerilen düzeltme ve gerekli regresyon testi
- Tahmini efor ve bağımlılıklar

Kanıt durumları: `Doğrulandı`, `Kısmen doğrulandı`, `Şüpheli`, `Tekrar üretilemedi`, `Yetersiz kanıt`, `Yanlış alarm`, `Düzeltildi`, `Doğrulandı ve kapatıldı`.

Kanıt olmadan bir konu kesin hata olarak yazılmaz. Bulgu kimlikleri tekrar kullanılmaz; şu ön ekler kullanılır: `ARCH-`, `FLOW-`, `LOGIC-`, `BUG-`, `SEC-`, `DATA-`, `CONC-`, `WORKER-`, `TEST-`, `PERF-`, `MIG-`, `OPS-`, `DOC-`.

## Debugging kuralları

Her hata için şu sıra izlenir:

1. Belirtiyi ve beklenen davranışı kaydet.
2. Hatayı tekrar üret.
3. İlgili log ve stack trace’i incele.
4. Veri ve kontrol akışını izle.
5. Kök nedeni doğrula.
6. Mümkünse önce başarısız regresyon testi yaz.
7. En küçük güvenli düzeltmeyi uygula.
8. İlgili ve bağlantılı testleri çalıştır.
9. Tüm test paketinde regresyon kontrolü yap.
10. Değişiklikleri ve sonucu kaydet.

Sadece semptomu gizleyen geçici düzeltmeler uygulanmaz; hata düzeltilirken ilgisiz refactor yapılmaz.

## Test ve kod değişikliği kuralları

Test komutları tahmin edilmez. Önce `Makefile`, `pyproject.toml`, `requirements*.txt`, `tox.ini`, `noxfile.py`, `package.json`, `pom.xml`, `build.gradle`, `Dockerfile`, `docker-compose*.yml` ve `.github/workflows/` içindeki tanımlı gerçek komutlar araştırılır.

Her test çalıştırmasında komut, çalışma dizini, ortam, exit code, süre, geçen/başarısız/atlanan test sayıları ve hata özeti kaydedilir. Doğrulanmış her bug için mümkün olduğunda regresyon testi bulunur.

Kod değişikliği ancak ilgili analiz fazı tamamlandıktan ve düzeltme aşamasına geçildikten sonra yapılır. Her düzeltme küçük, izole, geri alınabilir, test edilebilir ve belgelenmiş olmalıdır. Bir seferde bağımsız çok sayıda sorun düzeltilmez; otomatik formatlama nedeniyle ilgisiz yüzlerce satır değiştirilmez.

## Audit kayıtları

`.audit/` çalışma kaydıdır. Aşağıdaki dosyalar güncel tutulur:

- `README.md`: audit sistemi, dosya sorumlulukları ve faz sırası
- `CURRENT_PHASE.md`: aktif faz, durum, tarih, son/sıradaki görev, blocker ve bulgu sayıları, kod değişikliği izni
- `BASELINE.md`: commit, branch, ortam, build, test, lint, type-check ve runtime sonuçları
- `INVENTORY.md`: dizinler, diller, framework’ler, entry point’ler, servisler ve bağımlılıklar
- `SYSTEM_MAP.md`: bileşenler ve bağımlılıkları
- `DATA_FLOWS.md`: kritik akışların auth, validation, persistence, failure/retry/transaction/izolasyon ve test bilgileri
- `FINDINGS.md`: standart bulgular ve önem seviyeleri (`Kritik`, `Yüksek`, `Orta`, `Düşük`, `Bilgi`)
- `BUGS.md`: tekrar üretilebilir runtime ve iş mantığı hataları
- `FIX_PLAN.md`: önceliklendirilmiş düzeltme planı
- `TEST_MATRIX.md`: risk, test seviyesi, senaryolar, sonuç ve bulgu eşlemesi
- `COMMAND_LOG.md`: tarih, amaç, maskelenmiş komut, dizin, exit code ve sonuç
- `CHANGELOG_AUDIT.md`: audit sırasındaki kod, test, config ve dokümantasyon değişiklikleri
- `DECISIONS.md`: ADR benzeri karar, bağlam, seçenek, gerekçe, sonuç ve geri alma yöntemi
- `BLOCKERS.md`: production’ı engelleyen doğrulanmış sorunlar
- `DEFERRED.md`: ertelenen ancak kaybolmaması gereken konular
- `PRODUCTION_READINESS.md`: build, mimari, iş mantığı, veri bütünlüğü, güvenlik, testler, performans, migration, backup/restore, observability, Docker, CI/CD, deployment, rollback ve operasyon dokümantasyonu değerlendirmesi

Production-readiness kararı yalnızca `GO`, `CONDITIONAL GO` veya `NO-GO` olarak verilir; değerlendirme yapılmadıysa açıkça `Henüz değerlendirilmedi` yazılır.

## Mevcut raporları koruma

`REPORT.md`, `REPORT_UNDOCUMENTED.md`, `REPORT_CLOSING.md`, `ARCHITECTURE.md`, önceki audit/analiz promptları ve bulgu raporları silinmez ya da değiştirilmez. Bunlar ileride karşılaştırma ve doğrulama kaynağıdır.

## Raporlama dili

Raporlar ve kullanıcı açıklamaları Türkçe yazılır. Kod, sınıf, fonksiyon, komut, dosya yolu ve gerekli teknik terimler İngilizce kalabilir.
