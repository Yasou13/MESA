# Full-suite Hang / Timeout Investigation

Tarih: 2026-07-20  
Kapsam: kullanıcı bildirimi — full suite yaklaşık 22+ dakika `%8` seviyesinde kaldı.

## İlk süreç güvenliği

İlk incelemede bu run'a ait çalışan `pytest`, Python worker, uvicorn veya bağlı child
process bulunmadı. Dinleyen soketler yalnız sistem DNS/CUPS soketleriydi. Bu nedenle
yanlış süreci öldürme riskiyle `kill` gönderilmedi. Yarıda kesilen ilk bounded komut da
sonraki kontrolde tamamlanmış görünüyordu; pytest/child/listener kalıntısı bırakmadı.

Önceki takılan full-suite prosesinin canlı stdout/stderr'i veya PID'si erişilebilir
olmadığından o prosesin son test adı geriye dönük çıkarılamaz. Bu bir başarı sonucu
değil, kayıp çalışma-zamanı kanıtıdır.

## Koleksiyon ve ilk %10

Güvenli seçim: `--ignore=tests/test_mem0.py` (ayrıca `go_live_proofs` ve `bench`
hariç). `--deselect` kullanılmadı.

| Kontrol | Sonuç |
|---|---|
| Bounded collect-only | 900 test, timeout yok |
| İlk %10 node sırası | İlk 90 node: `test_adapter.py`, `test_adapter_factory.py`, `test_adapter_live.py`, `test_adapters.py`, `test_adaptive_router.py`, `test_api_router.py`, `test_api_schemas.py` |
| Dosya sınırı notu | Dosya bazlı binary split, dosyayı bölmemek için ilk %10 sınırının dışına taşarak toplam 113 test kapsadı. |

## Bounded file-binary sonuçları

Her blok `-vv --durations=25`, model/provider/dotenv kapalı ve audit storage altında,
`timeout --signal=TERM --kill-after=10s 180s` ile çalıştırıldı.

| Bölüm | Dosyalar | Sonuç | Son görülen/tamamlanan test | Sınıflandırma |
|---|---|---|---|---|
| A | `test_adapter.py`, `test_adapter_factory.py`, `test_adapter_live.py`, `test_adapters.py` | `40 passed in 6.55s` | `tests/test_adapters.py::test_adapter_factory_auto_detect_zero_cost` | PASS |
| B | `test_adaptive_router.py`, `test_api_router.py`, `test_api_schemas.py` | `73 passed in 9.33s` | `tests/test_api_schemas.py::TestCrossCutting::test_json_serialization_roundtrip` | PASS |

Hiçbir dosya veya test timeout'a ulaşmadı; dolayısıyla bu bölge için
`HANG_OR_TIMEOUT` kaydı **yoktur**. Beklenen davranış bounded süre içinde tamamlanma,
gerçek davranış da budur. Açık thread/task/process stack'i alınacak bir timeout olayı
oluşmadı.

## Sonraki işlem

Full suite doğrudan yeniden başlatılmadı. Kullanıcı bildirimi ile bu ilk bölge arasında
tekrar üretilebilir bir ilişki kurulamadı. Yeni bir hang görülürse PID/stdout ve son
`-vv` node id korunarak yalnız o dosya/test aynı bounded prosedürle `HANG_OR_TIMEOUT`
olarak kaydedilmelidir.
