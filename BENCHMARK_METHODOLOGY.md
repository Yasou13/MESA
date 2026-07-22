# MESA Benchmark Metodolojisi

## Amaç ve karşılaştırma birimi

Karşılaştırılan bileşen bellek/retrieval sistemidir. Cevap üretme değişkenini sabitlemek için MESA, Mem0, Zep ve Letta aynı generator modelini kullanır. Her gözlem anahtarı `seed + iteration + scenario_id + question_id` birleşimidir.

## İki ayrı sonuç hattı

### Retrieval

- Runner bütün adaptörlerde `Top‑K = 5` uygular.
- `Hit@1`, `Hit@3`, `Hit@5`, MRR ve nDCG@5 yalnızca `expected_context_ids` bulunan sorularda hesaplanır.
- Latency, adaptörün retrieval çağrısını ölçer; ortak generation süresi bu değere eklenmez.
- BEAM’in kaynak verisi context ID relevance etiketi vermediği için retrieval metrikleri `N/A`’dır.

### Full‑QA

- Sıralı Top‑5 context ortak Ollama generator’a verilir.
- Normalized exact match ve token F1 deterministik hesaplanır.
- Semantic judge yanıtı strict `{is_correct: bool, score: 0..1, reasoning: str}` şemasına uymalıdır.
- Ensemble gerçek boolean majority vote kullanır. Quorum sağlanmazsa koşum geçersizdir.
- Multi-model judge adı farklı en az iki model ister. Model listesinde aynı etiketi tekrarlamak bağımsızlık sayılmaz.
- Generation latency ve prompt/completion token sayıları retrieval değerlerinden ayrı tutulur.

## Geçerlilik

Purge, ingest, query, provider timeout, generator ve judge hataları `TIMEOUT_OR_ERROR` altyapı hatasıdır. Hatalı soru sonuç dosyasında tanı bilgisiyle kalır fakat koşum `invalid` olur ve process non-zero döner. Başarısız ingestion boş cevap olarak değerlendirilmez.

Kanıt seviyesi:

- `invalid`: en az bir altyapı/judge hatası.
- `provisional/self-judged`: generator yok, bağımsız semantic judge gerçekten çalışmadı veya judge generator ile aynı model.
- `publishable`: sıfır altyapı hatası, Full‑QA generator ve ondan farklı semantic judge fiilen çalıştı.

Sentetik comprehensive/mini setler `internal-regression-only` sınıfındadır; kanıt seviyesi dış benchmark niteliği kazandırmaz.

## Embedding ve graph ingest

MESA, `sentence-transformers/all-MiniLM-L6-v2` semantic embedding modelini açıkça yükler. Model bulunamazsa deterministic/hash fallback yasaktır ve setup fail-fast sonlanır.

Bir senaryonun context’leri iki geçişte yüklenir:

1. Bütün entity node’ları ve gerçek node ID’leri oluşturulur.
2. Relation `source`/`target` adları bu node’lara çözülür ve edge’ler eklenir.

Dataset doğrulayıcı duplicate kimlikleri, eksik `expected_context_ids` referanslarını ve çözülemeyen relation target’larını reddeder.

## Timeout ve izolasyon

SDK/provider timeout adaptör ve Ollama client seviyesinde uygulanır. Runner detached worker thread oluşturan genel bir hard-deadline wrapper kullanmaz; native timeout hatasını altyapı hatası olarak işaretler. MESA async çağrıları adaptera ait tek, `close()` ile kapatılan event-loop worker üzerinde yürür. Her iteration başında purge zorunludur; Mem0 önceki user namespace’ini fiziksel olarak siler ve purge doğrulanamazsa sonuç üretimi durur.

P95/P99 latency yalnız en az 20 gözlemde nearest-rank yöntemiyle hesaplanır. Daha küçük sample’larda değer `N/A`dır ve yayınlanabilir percentile kanıtı sayılmaz.

## Tekrarlanabilirlik ve resume

- Seed, adapter/generator setup’tan önce Python ve NumPy’ye uygulanır.
- Her seed ayrı sonuç dizini ve manifest kullanır.
- Manifest; seed, Top‑K, effective config SHA‑256, config dosyası SHA‑256, dataset SHA‑256 ve model etiketlerini içerir.
- Resume config/dataset hash eşleşmesi olmadan reddedilir.
- Question-level deduplication, append-only sonuç JSONL’sinden resume başında yeniden kurulur; state dosyası her soruda yeniden yazılmaz.
- Multi-seed raporu gerçek mean, sample std, standard error ve Student‑t %95 CI üretir.
- Baseline karşılaştırması ortak seed’ler ve aynı soru anahtarlarında paired fark/test; ayrıca seed agregatlarında Welch testi verir.
- Herhangi bir seed başarısızsa multi-seed komutu non-zero döner.

## Dataset provenance

BEAM ve LoCoMo kaynakları, revision, checksum ve lisansları `mesa-benchmark/datasets/SOURCES.json` içinde tutulur. LoCoMo converter resmi category‑5 davranışını uygular: cevabı bulunmayan adversarial sorularda ground truth `Not mentioned` olur ve retrieval relevance hesaplanmaz. Resmi kaynakta çözülemeyen iki evidence referansı manifestte belgelenir.

## Yayınlama kontrol listesi

Bir sonuç ancak şu koşullarla dışarı sunulmalıdır:

1. `config-check`, `dataset-check` ve `ollama-preflight` başarılı.
2. Mini MESA ve baseline koşumları başarılı.
3. Comprehensive ve aynı seed’li baseline tamamlanmış.
4. Sonuç `valid=true`; altyapı hatası sıfır.
5. Kullanılan dataset external benchmark ve lisans kullanıma uygun.
6. Generator’dan farklı semantic judge fiilen çalışmış.
7. Manifest, raw JSONL ve multi-seed raporu birlikte korunmuş.

## Paket sınırı

`mesa_benchmark`, MESA ve haricî memory sistemlerinin eşit Top-K ve ortak generator altında karşılaştırıldığı benchmark ürünüdür. `mesa_evals` ise MESA çekirdeğinin sentetik/golden-dataset ve CI regresyon paketidir; iki paket birbirinin metric veya sonuç boru hattını çağırmaz.
