
### Yeni Bir Nüans — mesa-benchmark Artık Çalışır Ama Yanlış Şeyi Ölçüyor

`mesa_client.py` artık gerçek API'yi çağırıyor, ama `answer()` metodunda `search_memory(..., include_graph=True)` ismi yanıltıcı. Kodu okudum: bu parametre **KuzuDB'ye hiç dokunmuyor** — sadece SQLite `nodes` tablosundan relational metadata (entity_name, content_payload) çekip vektör sonucunu zenginleştiriyor. Yani bu yeni benchmark, çalıştırılsa bile MESA'nın gerçek farkı olan **multi-hop graph traversal, PageRank quarantine, spreading activation**'ı hiç test etmiyor — sadece "Vector + SQLite metadata" ölçüyor. Bu, "BareRAG-plus-metadata" ile aynı kategoride, MESA'nın iddia ettiği differentiation'ı kanıtlamıyor.

Ayrıca bu paket **hâlâ hiç çalıştırılmamış** — sonuç dosyası yok. Import bug'ı düzeldi ama gerçek bir run kanıtı hâlâ yok.
---

### Sorun 1 — Kimse Bilmediği Bir Benchmark'a Güvenmez

"MESA %90, BareRAG %95" dediğinde uluslararası bir okuyucunun ilk sorusu: **"Bu rakam neye göre iyi/kötü?"** Kendi ürettiğin 20 senaryoluk dataset'in hiçbir referans noktası yok.

**Çözüm — tanınan benchmark'lara karşı koş, kendi benchmark'ını değil:**

Bu konuşmanın başında araştırdığımız LoCoMo (Mem0'ın ECAI 2025 paper'ında kullandığı), LongMemEval, MemoryArena — bunlar sektörün ortak referans noktaları. MESA'yı bunlardan birine karşı çalıştırıp "MESA, LoCoMo benchmark'ında X puan aldı, Mem0'ın kendi yayımladığı Y puanıyla karşılaştırılabilir" dediğinde, herkes anında ne kastettiğini anlıyor. Kendi ürettiğin dataset asla bu güveni vermiyor.

```
Öncelik: LoCoMo'yu indir, MESA'yı ona karşı çalıştır (İngilizce, genel domain)
Türk hukuku benchmark'ını "vertical showcase" olarak sakla (KVKK/local-first hikayesi için değerli)
```

---

### Sorun 2 — Keyword Matching Uluslararası Standart Değil

`any_of` keyword matching pratik ve ucuz ama akademik/endüstri standardı değil. RAGAS'ın dört metriği (Context Recall, Context Precision, Faithfulness, Answer Relevance) referans nokta.

**Minimum viable çözüm — ikisini birlikte raporla:**

python

```python
# Her senaryo için iki skor:
keyword_score = evaluate_keywords(answer, ground_truth)  # ucuz, hızlı, mevcut
judge_score = await llm_judge(answer, ground_truth, context)  # pahalı ama standart

# İkisi arasındaki uyum oranını da raporla — bu senin keyword proxy'nin
# güvenilirliğini kanıtlar:
agreement_rate = compute_agreement(keyword_score, judge_score)
```

Agreement rate yüksekse (%85+), "keyword matching bizim ölçeğimizde LLM-judge ile uyumlu, ucuz proxy olarak kullanılabilir" diyebilirsin — bu metodolojik bir savunma, uluslararası okuyucu bunu ciddiye alır.

---

### Sorun 3 — Tek Run, Varyans Yok

Şu an MESA %90 dediğinde bu tek bir çalıştırmanın sonucu. LLM'ler stokastik — aynı benchmark'ı 5 kere çalıştırırsan farklı sayılar çıkar. Uluslararası standart:

```
5 seed ile çalıştır (temperature=0 olsa bile LLM API'leri deterministik değil)
Rapor: 90.0% ± 2.1% (mean ± std, n=5)
Fark iddia ediyorsan (MESA > BareRAG): paired bootstrap test veya t-test ile p-value raporla
```

20 senaryoluk bir dataset'te %90 vs %95 farkı **istatistiksel olarak anlamsız olabilir** — bu 18/20 vs 19/20 demek, tek bir senaryo farkı. Bunu iddia etmeden önce test etmen lazım.

---

### Sorun 4 — Dataset Ölçeği Çok Küçük

20 senaryo hiçbir yayında ciddiye alınmaz. Bu konuşmanın başında konuştuğumuz sayı hâlâ geçerli: **en az 200, ideal 500+**. Ayrıca dört zorluk katmanı (single-hop, multi-hop, hard-negative, out-of-domain) olmalı — daha önce konuştuğumuz `test.md` planındaki %40/%30/%15/%15 dağılımı.

---

### Sorun 5 — Reproducibility Paketi Yok

Uluslararası bir okuyucu/hakem senin sonuçlarını **kendi makinesinde tekrar üretebilmeli**. Şu an:

```
Eksik: pinned dependency versions (requirements-lock.txt)
Eksik: seed control dokümantasyonu
Eksik: tek komutla çalıştırma (docker run mesa-benchmark --reproduce)
Eksik: dataset'in kendisinin public, lisanslı, versiyon numaralı hali
```

Bu paket olmadan hiç kimse iddianı doğrulayamaz — ve doğrulanamayan sonuç uluslararası camia için değersizdir.

---

### Sorun 6 — Bağımsız Değerlendirme Yok

Ground truth'u sen ürettin, sen skorladın. Bu **self-grading bias**. Çözüm:

```
Ground truth'u üreten ile skorlayan farklı olmalı (senin ürettiğin, 
başka bir LLM veya insan doğrulasın)
LLM-judge kullanıyorsan tek model değil, 2-3 farklı model kullan, 
aralarındaki uyumu (Cohen's kappa) raporla
```

---

### Sorun 7 — Rakip Kapsamı Dar

Sadece Mem0 + BareRAG uluslararası okuyucu için yetersiz. Bu konuşmada isimlerini geçirdiğimiz Zep, Letta/MemGPT, LangGraph Memory — en az ikisi daha eklenmeli. "MESA sadece bir rakibi geçti" değil "MESA sektördeki 5 sistemden 4'ünü geçti, birinde yakın" hikayesi çok daha güçlü.

---

### Sorun 8 — Yayın Altyapısı Yok

Sonuç raporun sadece repo'da bir markdown dosyası. Uluslararası görünürlük için:

```
1. HuggingFace Dataset olarak yayımla (dataset card, lisans, versiyon)
2. HuggingFace Space'te canlı leaderboard kur — herkes tıklayıp görebilsin
3. arXiv'e kısa bir technical report yükle (paper olması şart değil, 
   preprint bile metodoloji şeffaflığını gösterir)
4. r/LocalLLaMA'ya metodolojiyi tam açıklayarak paylaş — topluluk 
   eleştirisi kendisi bir doğrulama mekanizması
```

---

### Öncelik Sırası — Gerçekçi Yol Haritası

|Aşama|Ne|Süre|Etki|
|---|---|---|---|
|1|LoCoMo'ya karşı çalıştır (İngilizce, tanınan referans)|1 hafta|En yüksek — karşılaştırılabilirlik|
|2|5-seed varyans + significance test ekle|2-3 gün|İstatistiksel savunulabilirlik|
|3|LLM-judge + keyword agreement rate raporla|3-4 gün|Metodolojik güven|
|4|Dataset'i 200+'a çıkar (4 zorluk katmanı ile)|1-2 hafta|Ciddiye alınabilirlik|
|5|Zep + Letta ekle|1 hafta|Kapsam genişliği|
|6|HuggingFace Dataset + Space leaderboard|2-3 gün|Görünürlük|
|7|Reproducibility paketi (pinned deps, docker, seed)|2-3 gün|Doğrulanabilirlik|

---

### Minimum Viable "Uluslararası Kredibilite" Nedir

Hepsini birden yapmana gerek yok. Şu üçü olursa, uluslararası bir okuyucu ciddiye almaya başlar:

1. **LoCoMo sonucu** (karşılaştırılabilirlik)
2. **Varyans + p-value** (istatistiksel dürüstlük)
3. **HuggingFace'te açık dataset + reproducible script** (doğrulanabilirlik)

Bu üçü olmadan, ne kadar iyi sayı elde edersen et, "kendi benchmark'ında kendini test etmiş bir proje" olarak kalırsın — ki bu bu konuşma boyunca defalarca gördüğümüz tuzağın ta kendisi.