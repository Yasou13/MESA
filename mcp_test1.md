# MESA MCP Derin Uçtan Uca Sistem Doğrulama Görevi

Bu repoda çalışan MESA sisteminin ve bağlı MESA MCP Server'ın bütün temel ve ileri seviye
parçalarını uçtan uca test et.

Amaç yalnızca MCP araçlarının listelendiğini görmek değil; gerçekten veri yazıldığını, kalıcı
olarak saklandığını, doğru şekilde aranabildiğini, çok-adımlı (multi-hop) ilişkilerle geri
getirilebildiğini, consolidation/decay/çelişki gibi ileri mekanizmaların gerçekten çalıştığını,
ve sistemin eşzamanlılık/ölçek altında dayanıklı olduğunu kanıtlamaktır.

## Temel kurallar

- Önce repoyu ve mevcut mimariyi incele.
- Mevcut kodu gereksiz yere değiştirme.
- Test sırasında production verilerini silme veya bozma.
- Rastgele ve benzersiz bir test kimliği oluştur: `MESA_E2E_<timestamp veya uuid>`
- Bütün test kayıtlarını bu kimlikle işaretle.
- Mock, sahte cevap veya yalnızca fonksiyonun çağrılabilmesini başarı olarak kabul etme.
- Her sonucu somut çıktı, kayıt, response, log veya kod referansı (dosya + satır) ile doğrula.
- Bir hata bulursan üstünü kapatma; nedeni, ilgili dosyayı ve olası çözümü raporla.
- Önce test et, hemen kod değiştirme. Düzeltme gerekirse önce öneri sun; test tamamlanmadan
  kapsamlı refactor yapma.
- Bir testin "geçtiğini" iddia etmeden önce şu soruya kaynak dosya + satır numarasıyla cevap ver:
  "Bu sonucu üreten kod yolu gerçekten ne?" Bir bileşen çağrılıyor gibi görünüp aslında daha basit
  (ve yanlış) bir kod yoluna düşüyor olabilir — her PASS iddiasında bu ihtimali özellikle ele.
- Test edilmemiş bir parçayı başarılı olarak işaretleme. "Tool çağrıldı" sonucunu "sistem
  çalışıyor" kanıtı olarak kabul etme.

---

## 1. Sistem keşfi

Aşağıdakileri belirle:

- MESA MCP Server giriş noktası
- MCP araçlarının tanımlandığı dosyalar
- MCP transport türü: stdio, HTTP, SSE veya başka bir yöntem
- MESA API katmanı
- Storage katmanı
- Vector storage / embedding katmanı
- Graph storage katmanı (KuzuDB)
- Metadata ve persistence mekanizması (SQLite WAL)
- ConsolidationLoop / Tier-3 dual-LLM consensus mekanizması
- Epistemic uncertainty / PageRank quarantine mekanizması
- Spreading activation / fan-effect normalization mekanizması
- Decay (Ebbinghaus) mekanizması
- Worker veya background job yapısı
- Konfigürasyon dosyaları ve gerekli environment değişkenleri
- Health-check mekanizması
- Test klasörleri ve mevcut entegrasyon testleri
- Benchmark istemcisi (mesa-benchmark) ve `answer()` metodunun implementasyonu

Sonuçları kısa bir mimari akış halinde göster:

`Antigravity → MCP Server → MESA Service/API → Memory Pipeline → Storage/Vector/Graph → Retrieval (HybridRetriever) → Consolidation/Decay`

Her aşamada kullanılan gerçek dosya ve sınıfları belirt.

---

## 2. MCP araç keşfi

Bağlı MCP server üzerinden kullanılabilir bütün MESA araçlarını listele.

Beklenen temel araçlar:

- `mesa_health`
- `mesa_store_memory`
- `mesa_search_memory`
- `mesa_get_context`
- `mesa_get_memory`

Her araç için şunları doğrula:

- Araç gerçekten MCP server tarafından sunuluyor mu?
- Parametre şeması geçerli mi?
- Zorunlu alanlar doğru tanımlanmış mı?
- Geçersiz parametrede anlaşılır hata dönüyor mu?
- Sonuç formatı tutarlı mı?

Araç isimleri farklıysa gerçek isimleri tespit ederek testlere devam et.

---

## 3. Health testi

`mesa_health` aracını çağır ve aşağıdakileri doğrula:

- MCP server erişilebilir mi?
- MESA ana servisi erişilebilir mi?
- Storage hazır mı?
- Vector engine hazır mı?
- Graph engine hazır mı?
- Gerekli worker veya bağımlılıklar hazır mı?
- Health sonucu yalnızca sabit bir `"ok"` cevabı mı, yoksa gerçek bağımlılıkları kontrol ediyor mu?

Bağımlılıklardan biri çalışmıyorsa health-check'in bunu doğru şekilde raporlayıp raporlamadığını
kontrol et.

---

## 4. Memory yazma testi

Benzersiz test kimliğini içeren en az 5 farklı memory kaydı oluştur.

Örnek veri çeşitleri:

1. `architecture` — "MESA_E2E_ID projesinde ana graph engine olarak Kuzu kullanılmaktadır."
2. `decision` — "MESA_E2E_ID için retrieval sırasında hybrid vector + graph yaklaşımı seçildi."
3. `constraint` — "MESA_E2E_ID production ortamında destructive migration çalıştırılmayacak."
4. `error` — "MESA_E2E_ID testinde VectorEngine truthiness kontrolü uyarı üretti."
5. `preference` — "MESA_E2E_ID için teknik raporların Markdown formatında hazırlanması tercih edildi."

Her kayıt için:

- MCP aracı üzerinden yaz.
- Dönen memory ID'yi kaydet.
- Oluşturma zamanını ve metadata'yı doğrula.
- Kayıtların yalnızca MCP cevabında değil, gerçek storage katmanında bulunduğunu doğrula.
- Aynı verinin yanlışlıkla iki kez yazılıp yazılmadığını kontrol et.
- Memory tiplerinin ve metadata alanlarının korunup korunmadığını kontrol et.

---

## 5. Doğrudan ID ile okuma testi

Yazılan her memory kaydını dönen ID üzerinden `mesa_get_memory` ile çağır.

Şunları doğrula:

- Doğru kayıt dönüyor mu?
- İçerik birebir korunmuş mu?
- Memory tipi korunmuş mu?
- Metadata korunmuş mu?
- Olmayan bir ID için doğru hata dönüyor mu?
- Başka bir kaydın yanlışlıkla dönmesi gibi ID izolasyon problemi var mı?

---

## 6. Semantic search testi

`mesa_search_memory` ile farklı sorgular çalıştır:

- "Bu projede hangi graph engine kullanılıyor?"
- "Destructive migration konusunda alınan karar nedir?"
- "Retrieval yaklaşımı nasıl tasarlandı?"
- "Rapor formatı tercihi nedir?"
- "VectorEngine ile ilgili hangi hata kaydedildi?"

Her sorgu için:

- Beklenen memory ilk sonuçlarda geliyor mu?
- Relevance score varsa mantıklı mı?
- Sonuçlar test kimliği dışındaki ilgisiz kayıtlarla karışıyor mu?
- Exact keyword olmadan semantik eşleşme çalışıyor mu?
- Memory type filtresi varsa doğru çalışıyor mu?
- Limit/top-k parametresi doğru uygulanıyor mu?
- Aynı kayıt tekrar tekrar dönüyor mu?
- Boş sorgu ve anlamsız sorgu doğru yönetiliyor mu?

Sonuçları beklenen ve gerçekleşen şeklinde karşılaştır.

---

## 7. Context oluşturma testi

`mesa_get_context` aracına şu göreve benzer bir sorgu ver:

> "MESA_E2E_ID projesinin retrieval mimarisini geliştir. Graph engine, migration kısıtları,
> bilinen VectorEngine problemi ve raporlama tercihini dikkate al."

Şunları doğrula:

- İlgili architecture kaydı geldi mi?
- Decision kaydı geldi mi?
- Constraint kaydı geldi mi?
- Error kaydı geldi mi?
- Preference kaydı geldi mi?
- Context tekrar eden kayıtlarla şişiyor mu?
- Alakasız memory'ler ayıklanıyor mu?
- Kaynak memory ID'leri veya provenance bilgisi korunuyor mu?
- Context token veya boyut limiti uygulanıyorsa doğru çalışıyor mu?

Context içinde eksik kayıt varsa sebebini araştır.

---

## 8. Persistence testi

Kalıcı depolamanın gerçekten çalıştığını doğrula.

Mümkün ve güvenliyse:

1. Test memory'lerini yaz.
2. MCP/MESA servis sürecini kontrollü şekilde yeniden başlat.
3. Health kontrolünü tekrar yap.
4. Aynı memory'leri ID ve semantic search ile tekrar çağır.

Şunları raporla:

- Restart sonrasında kayıtlar kaldı mı?
- Sadece process memory'sinde mi tutuluyordu?
- Storage dosyası veya veritabanı gerçekten güncellendi mi?
- Vector index restart sonrasında kullanılabilir mi?
- Graph ilişkileri restart sonrasında korunuyor mu?

Servisi yeniden başlatmak mevcut geliştirme ortamını bozacaksa restart yapma; bunun yerine
persistence katmanını doğrudan incele ve neden restart testi yapmadığını belirt.

---

## 9. Vector katmanı testi

Vector/embedding katmanını bağımsız olarak doğrula:

- Embedding gerçekten oluşturuluyor mu?
- Embedding boyutu tutarlı mı?
- Null veya boş embedding oluşuyor mu?
- Benzer iki kayıt yakın sonuç veriyor mu?
- Alakasız kayıt daha düşük skor alıyor mu?
- Vector index'e ekleme gerçekleşiyor mu?
- Index ile ana storage arasında ID eşleşmesi doğru mu?
- Silinmiş veya bulunmayan memory için orphan vector var mı?
- Aynı memory tekrar işlendiğinde duplicate vector oluşuyor mu?

Kodda şu tip riskleri özellikle kontrol et:

- `if vector_engine:` gibi truthiness kontrolleri
- `__len__` veya `__bool__` nedeniyle yanlış false sonucu
- Engine başlatılmadan sorgu yapılması
- Embedding provider hatasının sessizce yutulması

---

## 10. Graph/Kuzu katmanı testi

Kuzu tabanlı graph katmanını doğrula:

- Node oluşturuluyor mu?
- Memory ile node ID eşleşiyor mu?
- İlişki oluşturuluyor mu?
- İlgili memory'ler arasında bağlantı kuruluyor mu?
- Graph sorgusu gerçek sonuç döndürüyor mu?
- Duplicate node veya edge oluşuyor mu?
- Transaction başarısız olduğunda yarım kayıt kalıyor mu?
- Graph verisi restart sonrasında korunuyor mu?
- Kuzu connection ve database lifecycle doğru yönetiliyor mu?

Aşağıdaki ilişkinin oluşup oluşmadığını kontrol et:

- architecture → uses → Kuzu
- decision → applies_to → retrieval
- constraint → restricts → migration
- error → affects → VectorEngine
- preference → applies_to → reporting

Sistem bu ilişkileri otomatik çıkarmıyorsa bunu hata olarak değil, mevcut yetenek sınırı olarak
açıkça raporla.

---

## 11. Storage tutarlılık testi

Aynı memory kaydı için şu katmanları karşılaştır:

- Ana storage
- Metadata storage
- Vector index
- Graph storage
- MCP response

Kontrol et:

- ID'ler aynı mı?
- Eksik katman var mı?
- Bir katmanda bulunup diğerinde bulunmayan orphan kayıt var mı?
- Transaction veya rollback mekanizması var mı?
- Yazma sırasında bir katman hata verirse sistem tutarsız durumda kalıyor mu?

Mümkünse kontrollü bir başarısızlık senaryosu uygula. Production verisini bozma.

---

## 12. API ve MCP karşılaştırması

MESA'nın doğrudan API veya Python client erişimi varsa aynı işlemi:

- MCP üzerinden
- Doğrudan API/client üzerinden

çalıştır ve sonuçları karşılaştır.

Şunları doğrula:

- Aynı memory modeli kullanılıyor mu?
- MCP ek veri kaybına neden oluyor mu?
- Hata kodları doğru çevriliyor mu?
- MCP server gerçek MESA sistemini mi çağırıyor, yoksa ayrı/geçici bir storage mı kullanıyor?
- MCP ve ana sistem aynı veritabanına mı bağlı?

Bu bölüm özellikle önemlidir: Antigravity'nin gördüğü memory'lerin ana MESA sistemiyle aynı
storage içinde bulunduğunu kanıtla.

---

## 13. Hatalı giriş ve sınır testleri

Aşağıdaki durumları test et:

- Boş içerik
- Çok uzun içerik
- Geçersiz memory tipi
- Eksik zorunlu parametre
- Yanlış veri tipi
- Olmayan memory ID
- Unicode/Türkçe karakterler
- Aynı kaydın tekrar yazılması
- Eş zamanlı birkaç yazma işlemi
- Boş arama sorgusu
- Çok yüksek top-k değeri
- MCP server kapalıyken çağrı
- Storage erişilemezken çağrı

Sistem çökmemeli; anlaşılır ve kontrollü hata vermeli.

---

## 14. Multi-hop retrieval testi (kritik)

Amaç: aramanın gerçekten `HybridRetriever` üzerinden mi geçtiğini, yoksa arka planda doğrudan
SQLite DAO'ya mı düştüğünü kanıtlamak.

1. Birbirine yalnızca graph ilişkisiyle bağlı, tek başına aranınca bulunamayacak en az 3 memory
   zinciri oluştur (A → ilişkilidir → B → ilişkilidir → C), her biri farklı yüzeysel kelimelerle
   yazılmış olsun (kelime örtüşmesi minimum).
2. Yalnızca A'da geçen bir terimle sorgu at, ama cevabın C'deki bilgiyi gerektirdiği bir soru sor.
3. Çağrı zincirini izle: `mesa_search_memory` / `mesa_get_context` çağrısından itibaren hangi
   sınıf/metot zincirinin çalıştığını (stack trace, log, veya kod okuma ile) doğrula.
   - `HybridRetriever` gerçekten çağrılıyor mu, yoksa sadece import edilip kullanılmıyor mu?
   - Graph traversal (Kuzu sorgusu) gerçekten tetikleniyor mu?
   - Sonuç tek-hop (yalnızca vector similarity) ile mi, çok-hop (graph + vector) ile mi üretiliyor?
4. Aynı sorguyu (a) yalnızca vector search'ü zorlayarak, (b) hybrid retrieval'ı zorlayarak iki kez
   çalıştır ve sonuçları karşılaştır. Fark yoksa bu ciddi bir bulgudur — raporla.
5. Kodda net şekilde göster (dosya + satır): arama gerçekten `HybridRetriever` mı kullanıyor,
   yoksa doğrudan bir SQLite DAO çağrısı mı yapılıyor?

---

## 15. Benchmark harness doğrulama (kritik)

Amaç: `answer()` metodunun gerçekten bir LLM çağırıp çağırmadığını kanıtlamak — chunk'ları
birleştirip döndürme ihtimaline karşı.

1. `answer()` çağrısını mock olmayan gerçek bir soru ile çalıştır.
2. Ağ/istemci seviyesinde LLM çağrısı gerçekten yapıldı mı? (HTTP log, token sayımı, latency
   profili — chunk concatenation neredeyse anlık olur, gerçek LLM çağrısı gözle görülür gecikme
   ve token maliyeti üretir.)
3. Dönen cevabın chunk'ların birebir birleşimi mi, yoksa gerçekten üretilmiş/parafraze edilmiş bir
   metin mi olduğunu karşılaştır (string benzerliği, cümle yapısı farkı).
4. Kodda `answer()` implementasyonunu satır satır oku; LLM client çağrısının var olup olmadığını,
   varsa hangi koşulda atlanabildiğini (ör. except bloğunda sessiz fallback) doğrula.
5. Bulguyu net şekilde raporla: bug hâlâ mevcut mu, yoksa düzeldi mi (hangi dosya/satırla)?

---

## 16. Consolidation / Tier-3 dual-LLM consensus testi

1. Aynı konuda, biri diğerini kısmen çelişen/güncelleyen iki memory yaz (ör. "X teknolojisi
   kullanılacak" → sonra "X teknolojisinden Y'ye geçildi").
2. ConsolidationLoop'u tetikle (varsa manuel trigger, yoksa zamanlayıcıyı bekle veya kodda çağrılan
   yeri bul ve doğrudan çağır).
3. Doğrula:
   - Dual-LLM consensus gerçekten iki ayrı LLM çağrısı mı yapıyor, yoksa tek çağrıyı mı taklit
     ediyor?
   - Konsolidasyon sonrası hangi memory "güncel" işaretleniyor, eski olan siliniyor mu,
     arşivleniyor mu, yoksa ikisi de aynı ağırlıkla mı kalıyor?
   - Consensus'a varılamazsa (iki LLM anlaşmazsa) sistem ne yapıyor — raporla, hata mı veriyor,
     sessizce mi geçiyor?

---

## 17. Epistemic uncertainty / PageRank quarantine testi

1. Düşük güvenilirlikte, çelişkili veya izole (graph'ta bağlantısı zayıf) bir memory yaz.
2. PageRank tabanlı belirsizlik skorlamasının bu kaydı gerçekten düşük skorla işaretleyip
   işaretlemediğini doğrula.
3. Bu kayıt "karantina"ya alınıyorsa: karantinadaki kayıt normal search/context sonuçlarına
   giriyor mu, girmiyor mu? Kullanıcı bunu görebiliyor mu, yoksa sessizce mi dışlanıyor?
4. Skorlamanın gerçekten graph topolojisinden hesaplandığını (sabit/varsayılan bir değer
   dönmediğini) kodda doğrula.

---

## 18. Spreading activation & fan-effect normalization testi

1. Bir merkezi kavrama (hub) çok sayıda (10+) ilgisiz veya zayıf ilişkili memory bağla.
2. Aynı hub'a az sayıda (2-3) güçlü ilişkili memory bağlayan ikinci bir senaryo oluştur.
3. Her iki senaryoda da hub üzerinden yapılan bir sorguda, fan-effect normalization'ın
   yüksek-bağlantılı hub'ın sinyalini "sulandırıp sulandırmadığını" karşılaştır.
4. Beklenti: az ama güçlü bağlantılı senaryoda ilgili memory'nin skoru, çok bağlantılı/sulandırılmış
   senaryodakinden orantısız derecede düşük çıkmamalı. Aksi bir sonuç varsa raporla.

---

## 19. Ebbinghaus decay / zamana bağlı unutma testi

1. Farklı "yaş"ta (mümkünse created_at alanını manipüle ederek veya gerçek zaman geçirerek)
   memory'ler oluştur.
2. Decay fonksiyonunun gerçekten çalışıp çalışmadığını doğrula: eski ve erişilmemiş bir memory'nin
   arama skorunda gerçek bir düşüş var mı?
3. Sık erişilen (mesa_get_memory ile tekrar tekrar okunan) bir memory'nin decay'e karşı
   "güçlendiğini" (spaced repetition benzeri) doğrula — bu davranış implement edilmemişse eksik
   yetenek olarak raporla, hata olarak değil.
4. Decay hesaplaması gerçek zaman damgasından mı, yoksa sabit/placeholder bir değerden mi
   türetiliyor — kodda göster.

---

## 20. Çelişki (contradiction) tespiti ve çözümü testi

1. Doğrudan birbiriyle çelişen iki memory yaz (ör. "Production'da destructive migration
   YASAKTIR" / "Production'da destructive migration test amaçlı bir kez çalıştırıldı").
2. `mesa_get_context` bu iki çelişkili kaydı aynı context'e koyuyor mu? Koyuyorsa, çelişkiyi
   işaretliyor mu, yoksa ikisini de "doğruymuş gibi" mi sunuyor?
3. Sistemde açık bir çelişki tespit mekanizması yoksa bunu mimari risk olarak raporla — düzenlenmiş
   sektör (hukuk bürosu MVP'si) için bu kritik bir gap'tir.

---

## 21. Ölçek ve yük testi

1. Aynı test kimliğiyle en az 200-500 sentetik memory yaz (script ile, tek tek elle değil).
2. Yazma throughput'unu ölç (kayıt/saniye), zamanla düşüş var mı izle.
3. 500 kayıt sonrası bir arama sorgusunun p50/p95 latency'sini, 5 kayıt varken ölçülenle
   karşılaştır. Doğrusal olmayan bir bozulma varsa (ör. tam tarama / index kullanılmıyor) raporla.
4. Vector index ve graph'ın bu ölçekte hâlâ tutarlı (orphan yok, duplicate yok) kaldığını bölüm
   9/10/11'deki yöntemlerle tekrar doğrula.

---

## 22. Eşzamanlılık ve WAL kuyruğu stres testi (Phantom Write)

1. Aynı anda (gerçek paralel, sırayla değil) 10+ yazma isteği gönder.
2. Hepsi WAL kuyruğuna doğru sırayla mı giriyor, yarış durumu (race condition) var mı?
3. Mümkünse, yazma sürerken servisi kontrollü olarak kes (kill -9 veya container restart) ve:
   - Kesme anındaki kaydın "hayalet" (ne var ne yok) durumda kalıp kalmadığını,
   - Restart sonrası WAL'ın kaldığı yerden devam edip etmediğini,
   - Aynı kaydın restart sonrası iki kez işlenip işlenmediğini (idempotency) doğrula.
4. Bu test riskli olduğu için development ortamını bozacaksa **yapma**; bunun yerine WAL kuyruğu
   implementasyonunu kod üzerinden incele ve hangi senaryoların teorik olarak güvenli/güvensiz
   olduğunu net şekilde raporla.

---

## 23. Rakip karşılaştırma tutarlılığı (Mem0 / Zep / Letta)

Eğer mevcut competitive benchmark altyapısı (Ollama LLM judge dahil) bu ortamda çalıştırılabiliyorsa:

1. Aynı test senaryosunu (aynı memory seti, aynı sorgular) MESA ve en az bir rakipte çalıştır.
2. LLM judge'ın MESA ve rakip için aynı kriterlerle, aynı prompt ile değerlendirildiğini doğrula
   (judge prompt'unda sisteme göre farklı toleranslar/istisnalar var mı — varsa bu adaletsiz
   karşılaştırma anlamına gelir, raporla).
3. Judge'ın kendisi de bir LLM olduğu için: aynı soruyu 2-3 kez tekrar çalıştırıp sonuçların
   tutarlı olup olmadığını (judge varyansı) ölç.

---

## 24. Güvenlik / izolasyon testi

1. Farklı bir "kullanıcı/oturum/tenant" kimliği ile ikinci bir test kaydı seti oluştur.
2. Birinci kimlikle yapılan arama/context çağrılarının ikinci kimliğin verilerini sızdırıp
   sızdırmadığını doğrula (bu, hukuk bürosu MVP'si için özellikle kritik bir gereksinimdir).
3. Böyle bir izolasyon mekanizması hiç yoksa (ör. tüm memory'ler tek global namespace'te) bunu
   "Critical" seviyede mimari risk olarak raporla — regüle sektör hedefiyle doğrudan çelişir.

---

## 25. Mevcut testleri çalıştır

Repodaki mevcut test yapısını tespit et ve uygun testleri çalıştır.

Örnek olarak, projeye uygunsa:

- Unit testler
- Integration testler
- MCP testleri
- Storage testleri
- Vector testleri
- Kuzu/graph testleri
- API testleri
- Worker testleri
- Benchmark testleri (mesa-benchmark)

Önce test komutlarını repodan belirle. Paket yöneticisini veya komutları tahmin etme.

Başarısız testlerde: test adı, hata mesajı, ilgili dosya/satır, muhtemel kök neden, MCP kullanımına
etkisi bilgilerini raporla.

---

## 26. Antigravity gerçek kullanım kanıtı

Antigravity'nin MESA MCP araçlarını gerçekten kullandığını kanıtla.

Şunları göster:

- Çağrılan MCP araçları
- Kullanılan parametrelerin güvenli özeti
- Dönen memory ID'leri
- Arama sonucunda bulunan kayıtlar
- Context sonucundaki kaynak kayıtlar
- MCP server loglarında ilgili çağrılar
- Ana storage içindeki karşılıkları

Yalnızca sohbet cevabını kanıt olarak kabul etme.

---

## 27. Test temizliği

Test sonunda yalnızca `MESA_E2E_ID` ile oluşturulan kayıtları temizleme imkânı varsa:

- Önce oluşturulan bütün test kayıtlarını listele.
- Temizleme işleminin güvenli olduğundan emin ol.
- Başka verileri silme.
- Delete özelliği yoksa test kayıtlarını silmeye çalışma.
- Silinmeyen test verilerini raporda açıkça belirt.

Persistence, ölçek ve concurrency testleri tamamlanmadan test kayıtlarını silme.

---

## 28. Son rapor

Repo kökünde şu dosyayı oluştur: `MESA_MCP_E2E_REPORT.md`

Rapor formatı:

```
# MESA MCP E2E Test Report

## Genel Sonuç

- Genel durum: PASS / PARTIAL / FAIL
- MCP bağlantısı:
- Memory write:
- Memory read:
- Semantic search:
- Context retrieval:
- Persistence:
- Vector storage:
- Graph/Kuzu:
- API–MCP consistency:
- Error handling:
- Multi-hop retrieval (gerçek kod yolu):
- Benchmark answer() LLM çağrısı:
- Consolidation Tier-3 consensus:
- Epistemic uncertainty quarantine:
- Fan-effect normalization:
- Decay davranışı:
- Çelişki tespiti:
- Ölçek (500 kayıt) latency profili:
- WAL/concurrency stres testi:
- Rakip karşılaştırma adaleti:
- Tenant/kullanıcı izolasyonu:
- Test suite:

## Test Matrisi

| ID | Bileşen | Test | Beklenen | Gerçekleşen | Durum | Kanıt |
|---|---|---|---|---|---|---|

## Bulunan Hatalar

Her hata için:
- Önem seviyesi: Critical / High / Medium / Low
- İlgili bileşen
- Dosya ve satır
- Tekrarlama adımları
- Beklenen davranış
- Gerçek davranış
- Muhtemel kök neden
- Önerilen çözüm

## Mimari Riskler

- Veri tutarsızlığı riskleri
- Sessiz hata riskleri
- Duplicate kayıt riskleri
- Persistence riskleri
- Vector–graph senkronizasyon riskleri
- MCP ile ana sistemin farklı storage kullanma riski
- Multi-hop retrieval'ın yanlış kod yoluna düşme riski
- Çelişki tespiti eksikliğinin regüle sektör (hukuk) kullanımına etkisi
- Tenant izolasyonu eksikliğinin prod/enterprise kullanımına etkisi
- Ölçek altında bozulma riskleri

## Kanıtlar

- Kullanılan test kimliği
- Oluşturulan memory ID'leri
- Çalıştırılan komutlar
- Önemli log parçaları
- Test çıktıları
- Storage doğrulamaları
- Latency/throughput ölçümleri

## Son Karar

Şu sorulara net cevap ver:

1. Antigravity, MESA MCP Server'a gerçekten bağlı mı?
2. MCP araçları gerçek MESA backend'ini kullanıyor mu?
3. Memory kayıtları gerçekten kalıcı mı?
4. Semantic search gerçekten embedding/vector katmanını kullanıyor mu?
5. Graph/Kuzu katmanı gerçekten veri yazıp okuyabiliyor mu?
6. Vector, graph ve ana storage arasında tutarlılık var mı?
7. Multi-hop retrieval gerçekten graph traversal kullanıyor mu, yoksa yalnızca vector similarity mi?
8. Benchmark'taki doğruluk sonuçları güvenilir mi, yoksa yanlış kod yolundan mı geliyor?
9. Consolidation gerçekten iki bağımsız LLM çağrısı yapıyor mu?
10. Sistemde çelişkili bilgi tespiti var mı? Yoksa, düzenlenmiş sektör MVP'si için engelleyici bir
    gap mi?
11. Tenant izolasyonu var mı? Yoksa bu, prod/enterprise kullanımını engelleyen kritik bir hata mı?
12. Sistem 500+ kayıtta hâlâ kabul edilebilir performans gösteriyor mu?
13. Sistem günlük geliştirme sırasında güvenle kullanılabilir mi?
14. Production kullanımını engelleyen kritik hata var mı?
```

---

## Çalışma sonunda

Sohbet içinde yalnızca kısa bir özet sun:

- Genel sonuç
- Kaç test geçti/kaldı
- Kritik hatalar (özellikle multi-hop, benchmark answer(), çelişki tespiti, tenant izolasyonu)
- Rapor dosyasının yolu

Test edilmemiş bir parçayı başarılı olarak işaretleme. "Tool çağrıldı" sonucunu "sistem çalışıyor"
kanıtı olarak kabul etme. Her PASS sonucu için somut bir kanıt üret.