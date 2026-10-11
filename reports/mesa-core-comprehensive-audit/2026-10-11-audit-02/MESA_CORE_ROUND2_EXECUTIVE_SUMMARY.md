# MESA Core Round 2 — Yönetici Özeti

## Sonuç

Geçici teknik hüküm: `MESA_CORE_ROUND2_REPAIRED_WITH_EXTERNAL_DEPENDENCIES_AND_PUSHED`.
Bu hüküm ancak rapor commit'i aynı dala itildikten ve uzak SHA doğrulandıktan sonra
nihai olur. MESA-owned olarak yeniden üretilebilen kritik kusurlar giderildi;
MESA_Data kaynak doğruluğu, gerçek Magibu/Ollama sağlayıcı kalitesi, resmi Profile B
ve uzun süreli native LanceDB bellek sertifikasyonu bu çalışmanın dış bağımlılıklarıdır.

## Gerçekleştirilen onarımlar

- `920c9a0`: doğrulanmış typed kanun/madde/sürüm kimliği API → mutation → fallback
  projection → canonical assertion → retrieval provenance zincirinde korundu.
- `4298a81`: yeni Alembic head'i rebuild preflight sözleşmesine eklendi; restart /
  migration / replay / cutover yolu yeniden açıldı.
- `736be21`: resmi Profile B'den ayrık, dondurulmuş DEV manifesti ile temporal ve
  restart holdout karşıörnekleri eklendi.
- `d7f5c23`: legal candidate üretimi compound index kullanan, scope ve temporal
  filtreli SQL'e taşındı; rebuild sonrasında typed kimlik sorgusu kanıtlandı ve
  public contract dokümante edildi.

Başlıca kaynaklar:

- `mesa_storage/legal_identity.py:186-259`
- `mesa_api/v4_router.py:219-261`
- `mesa_storage/dao.py:1903-1961, 5851-5885, 6750-6826, 7011`
- `mesa_workers/projection_worker.py:57-80`
- `mesa_storage/alembic/versions/d5e6f7a8b9c0_add_typed_legal_identity.py`

## Doğrulama özeti

- Minimal eski-yol reproducer'ları onarım öncesi: **2/2 FAIL**.
- Round 2 odak + holdout: **12 passed**.
- Komşu retrieval/migration/API: **98 passed**.
- Graph/vector/context/rebuild geniş matrisi: ilk koşuda eski rebuild-head sabiti
  nedeniyle **1 failed, 211 passed**; düzeltme sonrası ilgili yaşam döngüsü **18 passed**.
- API/scope/temporal/bounded soak matrisi: **112 passed**.
- Doğru üretim SQLite dayanıklılık varsayımlarıyla tek parça tam suite:
  **1948 passed, 0 failed, 128 warnings, 478.17 s**.
- Ruff, katman import kontrolü, mypy override ratchet, 138 production source için
  mypy, compile/import ve değişen 13 Python dosyası için Black: **PASS**.

`MESA_SQLITE_SYNCHRONOUS=OFF` zorlanarak yapılan ilk tam koşudaki sekiz durability
test hatası geçerli ürün sonucu değildir; override kaldırıldığında tam suite yeşildir.

## Kalan sınırlar

- Typed alanın şema/alias/auth doğrulaması MESA Core'a aittir; kaynağın gerçekten
  bildirilen mevzuata ait olduğunun doğrulanması MESA_Data/catalog sorumluluğudur.
- Ollama `127.0.0.1:11434` erişilebilir değildi; gerçek Magibu digest/dimension ve
  kalite ölçümü `NOT_RUN`.
- Beş saniyelik embedded/native smoke geçti; tarihsel 1.000-upsert / uzun süreli
  bellek iddiası için güvenli ölçekte sertifikasyon yapılmadı (`SOAK_NOT_RUN`).
- Resmi Profile B TEST verisi okunmadı, çalıştırılmadı ve tuning için kullanılmadı.
