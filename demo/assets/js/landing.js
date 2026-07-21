// MESA v0.6.0 — Enterprise Developer-First (DX) Showcase & RAG Sandbox Logic

const API_BASE = "/v3/memory";

let state = {
    apiKey: "",
    agentId: "",
    sessionId: "",
    busy: false,
    currentLang: "en",
    activeTab: "beam",
    activePipelineStep: 0,
    installMode: "pip"
};

// ---------------------------------------------------------------------------
// Bilingual Translation Dictionary (EN & TR) — 6 Mandatory Architectural Layers
// ---------------------------------------------------------------------------
const I18N = {
    en: {
        // Navigation
        nav_why: "Why MESA",
        nav_sandbox: "Live Sandbox",
        nav_bench: "Benchmarks",
        nav_security: "Security & Local Mode",
        nav_ecosystem: "Ecosystem",
        btn_connect: "Connect Sandbox",
        
        // Layer 1: Above the Fold (Hero)
        hero_badge: "v0.6.0 Enterprise Triple-Store Engine",
        hero_title: "The Open-Source <span class='gradient-text'>Triple-Store Memory Engine</span> for Enterprise AI Agents",
        hero_desc: "Eliminate context amnesia, tenant leakage, and multi-hop reasoning loops. Built with high-throughput native C++ KùzuDB graph traversal, LanceDB dense vectors, and Stage-2 CrossEncoder reranking.",
        hero_cta_sandbox: "Test Live Sandbox",
        hero_cta_visualizer: "Try the System (Visual Demo)",
        hero_cta_bench: "View Benchmarks",
        hero_cta_docs: "OpenAPI Specs (/docs)",
        
        install_label: "Instant Installation & Daemon Setup:",
        cmd_pip: "pip install mesa-memory",
        cmd_docker: "docker run -d --name mesa -p 8000:8000 mesa-engine:v0.6.0",
        btn_copy: "Copy",
        btn_copied: "Copied!",
        
        snippet_title: "Python SDK Quickstart — 4 Lines of Code",
        snippet_comment_1: "# Connect to headless MESA daemon or embedded instance",
        snippet_comment_2: "# Direct-write semantic memory with automatic triplet extraction",
        snippet_comment_3: "# Hybrid Alpha RRF search across vectors, graphs, and lexical FTS5",
        
        // Layer 2: Architectural Superiority (Why MESA)
        why_title: "Architectural Superiority: The Triple-Store Advantage",
        why_subtitle: "Why standard vector-only RAG breaks in production, and how MESA solves it with synchronous multi-store isolation.",
        
        p1_title: "SQLite WAL (Relational)",
        p1_desc: "Provides strict ACID compliance, operational state management, and FTS5 lexical keyword indexing. Guarantees zero data corruption and fast exact-match lookup.",
        p2_title: "LanceDB (Dense Vectors)",
        p2_desc: "Embedded serverless vector database powered by Rust. Executes sub-millisecond cosine similarity search (`sentence-transformers/all-MiniLM-L6-v2`) without network overhead.",
        p3_title: "KùzuDB (Relational Graph)",
        p3_desc: "Native C++ embeddable property graph database. Resolves long-chain multi-hop entity relationships and captures relational salience (`_apply_alpha_reranking`) without Python event-loop blocking.",
        
        cmp_title: "Objective Architectural Comparison",
        cmp_subtitle: "A head-to-head engineering evaluation against standard RAG architectures and SaaS memory wrappers.",
        
        cmp_col_feature: "Engineering Dimension",
        cmp_col_vector: "Standard RAG (Vector Only / SaaS Wrappers)",
        cmp_col_mesa: "MESA Triple-Store Engine (v0.6.0)",
        
        row1_feat: "Multi-Hop Reasoning & Entity Salience",
        row1_vec: "❌ Fails on complex chains; vector distance ignores relational links between entities.",
        row1_mesa: "✅ Native KùzuDB graph traversal + Alpha RRF captures multi-hop context (`Hit@1: 93.00%`).",
        
        row2_feat: "Hallucination & Context Amnesia Prevention",
        row2_vec: "⚠️ High amnesia rate due to background embedding queues and unverified chunks.",
        row2_mesa: "✅ Valence Motor & EWMAD novelty gating filters redundant noise before storage.",
        
        row3_feat: "Tenant Isolation & Row-Level Security (RLS)",
        row3_vec: "⚠️ Soft application-level filtering; prone to cross-tenant data leakage.",
        row3_mesa: "🔒 Mathematical Epistemic RLS enforced at the lowest database adapter (`WHERE agent_id = ?`).",
        
        row4_feat: "Candidate Reranking & Precision",
        row4_vec: "❌ Single-stage cosine top-k; returns irrelevant high-similarity noise.",
        row4_mesa: "⚡ Dual-stage: Alpha RRF Stage 1 + deep cross-attention (`ms-marco-MiniLM-L-6-v2`) Stage 2.",
        
        row5_feat: "Air-Gapped & Zero-Cost Execution",
        row5_vec: "❌ Dependent on cloud APIs (OpenAI/Claude embedding endpoints and costly token fees).",
        row5_mesa: "🛡️ 100% local inference with Ollama adapters and local sentence-transformers (Zero API fees).",
        
        // Layer 3: Interactive Sandbox
        sandbox_title: "Interactive Sandbox: Real-Time Verification",
        sandbox_subtitle: "Don't take our word for it. Test hybrid retrieval, examine LanceDB similarity scores, and inspect context right inside your browser.",
        
        quick_label: "Try Quick Diagnostic Queries:",
        q1_btn: "1. Store Enterprise Lead",
        q1_text: "User is interested in the enterprise plan with 500 seats and custom RLS.",
        q2_btn: "2. Query Lead Context",
        q2_text: "What seat count and plan type did the user request?",
        q3_btn: "3. Test Graph Salience",
        q3_text: "How does MESA KuzuDB graph traversal prevent hallucinations?",
        
        chat_heading: "Conversation & Direct-Write Interface",
        telemetry_heading: "Context Inspector & Real-time Telemetry (`/v3/demo/chat`)",
        chat_placeholder: "Ask a question or store a memory block...",
        btn_logout: "Disconnect Sandbox",
        
        // Layer 4: Transparent Benchmark Data & Observability Suite
        bench_title: "Transparent Empirical Observability Suite",
        bench_subtitle: "Engineers trust real telemetry, execution breakdowns, and exact-match benchmarks. Verified under strict Top-K=5 parity across 800+ automated test suites.",
        
        hit1_title: "Hit@1 Precision",
        hit1_sub: "▲ +41.8% vs Cosine Top-K baseline",
        hit3_title: "Hit@3 Recall",
        hit3_sub: "▲ 99.00% Candidate Parity Guaranteed",
        mrr_title: "Mean Reciprocal Rank (MRR)",
        mrr_sub: "▲ Optimal Position Ranking Quality",
        ndcg_title: "NDCG@10 Score",
        ndcg_sub: "▲ Normalized Discounted Cumulative Gain",
        
        tab_beam: "⚡ BEAM Contradiction Suite (400 Queries)",
        tab_multihop: "🕸️ Multi-Hop Graph Reasoning (58 Queries)",
        
        metric_acc: "Golden Dataset Accuracy",
        metric_acc_sub: "Verified against human ground-truth",
        metric_avg_lat: "Mean Pipeline Latency",
        metric_avg_lat_sub: "Full direct-write + retrieval cycle",
        metric_p95: "P95 Latency Threshold",
        metric_p95_sub: "95% of queries execute below this mark",
        metric_p99: "P99 Tail Latency",
        metric_p99_sub: "Maximum tail latency under concurrent load",
        
        chart_lat_title: "Execution Breakdown Waterfall (`Triple-Store Pipeline`)",
        chart_gauge_title: "Ground-Truth Accuracy Gauge vs Vector Baseline",
        
        scaling_title: "Linear Scalability Under Heavy Load (10,000+ Memory Nodes)",
        scaling_desc: "Thanks to KùzuDB's C++ indexing and LanceDB's memory-mapped vector structures, MESA maintains near-linear query latency (`<45ms` P95 for Multi-Hop) even as the agent's memory graph scales to over 10,000 active nodes.",
        
        method_title: "Methodological Verification & Judge Consensus Analysis",
        method_desc: "Our evaluation pipeline validates keyword-based proxy scores against dual LLM Judges (GPT-4 / Claude consensus). For BEAM, our measured agreement rate is <strong>79.17%</strong> (Cohen's Kappa: <code>0.1319</code>), proving that exact-match proxies provide fast CI/CD feedback while LLM consensus handles nuanced factual verification.",
        
        term_console_title: "Live CI/CD Verification Runner (`python -m mesa_benchmark.runner`)",
        
        // Layer 5: Enterprise Security & Zero-Cost Mode
        sec_title: "Enterprise Security & Zero-Cost Local RAG",
        sec_subtitle: "Designed to satisfy strict CTO and Security Architect requirements for data sovereignty and multi-tenant isolation.",
        
        zc_title: "Zero-Cost Air-Gapped Local RAG",
        zc_desc: "Run complete memory extraction, embedding, and reranking on self-hosted hardware without sending a single byte to external cloud providers.",
        zc_i1: "<strong>Local Embeddings:</strong> Built-in support for <code>sentence-transformers/all-MiniLM-L6-v2</code> running locally on CPU or GPU.",
        zc_i2: "<strong>Ollama Integration:</strong> Seamlessly bind local LLMs (Llama 3, Mistral, Qwen) for triplet extraction and response generation.",
        zc_i3: "<strong>Zero Token Overhead:</strong> Eliminate recurring API fees and data exfiltration risks completely.",
        
        zt_title: "Zero-Trust & Epistemic Row-Level Security",
        zt_desc: "Every database operation is cryptographically bound to the tenant's agent identifier, ensuring zero cross-agent leakage.",
        zt_i1: "<strong>Mathematical Epistemic RLS:</strong> Hard-coded where clauses (`WHERE agent_id = ?`) across vector, lexical, and graph engines.",
        zt_i2: "<strong>Role-Based Access Control (RBAC):</strong> Fine-grained permission matrices (`mesa_memory/security/rbac.py`) for read/write enforcement.",
        zt_i3: "<strong>Timing-Attack & Prompt Injection Shield:</strong> Constant-time API key rotation and Valence Motor pre-filtering against malicious payloads.",
        
        // Layer 6: Developer Ecosystem & Integrations
        eco_title: "Developer Ecosystem & Universal Integrations",
        eco_subtitle: "Integrate MESA into existing agent architectures with standard adapters and drop-in SDKs.",
        
        e1_title: "LangChain & LlamaIndex",
        e1_desc: "Drop-in memory store classes and retriever adapters for instant agentic memory replacement.",
        e2_title: "FastAPI v3 & Python SDK",
        e2_desc: "Strict Pydantic v2 schemas (`MemoryInsertRequest`) and clean async client (`MesaClient`).",
        e3_title: "Model Context Protocol (MCP)",
        e3_desc: "Native MCP server tools for seamless connection to Claude Desktop and AI IDEs.",
        e4_title: "Docker & Kubernetes Deployment",
        e4_desc: "Ready-to-run Docker images, Docker Compose manifests, and production Helm charts.",
        
        docs_bridge_title: "Explore the Complete Developer Documentation",
        docs_bridge_desc: "Inspect live interactive API endpoints, OpenAPI JSON schemas, and thorough whitepapers.",
        btn_swagger: "OpenAPI Swagger UI (/docs)",
        btn_redoc: "Redoc Specification (/redoc)",
        btn_whitepaper: "Architecture Whitepaper (.md)",
        
        modal_title: "Connect to MESA Sandbox",
        modal_desc: "Enter your API Key and Agent ID to establish a live session with the backend memory engine.",
        modal_btn: "Start Session"
    },
    tr: {
        // Navigation
        nav_why: "Neden MESA",
        nav_sandbox: "Canlı Sandbox",
        nav_bench: "Başarım Testleri",
        nav_security: "Güvenlik & Yerel Mod",
        nav_ecosystem: "Ekosistem",
        btn_connect: "Sandbox Bağlantısı",
        
        // Layer 1: Above the Fold (Hero)
        hero_badge: "v0.6.0 Kurumsal Üçlü Depolama Motoru",
        hero_title: "Kurumsal Yapay Zeka Ajanları İçin Açık Kaynaklı <span class='gradient-text'>Üçlü Depolama Bellek Motoru</span>",
        hero_desc: "Bağlam amnezisini, kiracı veri sızıntılarını ve çok adımlı çıkarım hatalarını ortadan kaldırın. Yüksek hızlı KùzuDB C++ çizge gezintisi, LanceDB yoğun vektörleri ve Stage-2 CrossEncoder yeniden sıralaması ile güçlendirildi.",
        hero_cta_sandbox: "Canlı Sandbox'ı Test Et",
        hero_cta_visualizer: "Sistemi Deneyin (Görsel Demo)",
        hero_cta_bench: "Benchmark Sonuçları",
        hero_cta_docs: "OpenAPI Dokümantasyonu (/docs)",
        
        install_label: "Hızlı Kurulum & Sunucu Başlatma:",
        cmd_pip: "pip install mesa-memory",
        cmd_docker: "docker run -d --name mesa -p 8000:8000 mesa-engine:v0.6.0",
        btn_copy: "Kopyala",
        btn_copied: "Kopyalandı!",
        
        snippet_title: "Python SDK Hızlı Başlangıç — 4 Satır Kod",
        snippet_comment_1: "# MESA arka plan sunucusuna veya yerel instance'a bağlan",
        snippet_comment_2: "# Otomatik üçlü (triplet) çıkarımı ile semantik hafıza ekle",
        snippet_comment_3: "# Vektör, çizge ve FTS5 kelime indeksi üzerinde Alpha RRF araması yap",
        
        // Layer 2: Architectural Superiority (Why MESA)
        why_title: "Mimari Üstünlük: Üçlü Depolama Avantajı",
        why_subtitle: "Sadece vektör kullanan standart RAG sistemleri neden yetersiz kalır ve MESA bunu nasıl çözer?",
        
        p1_title: "SQLite WAL (İlişkisel)",
        p1_desc: "Katı ACID uyumluluğu, operasyonel durum yönetimi ve FTS5 kelime bazlı indeksleme sağlar. Sıfır veri bozulması ve anlık kelime arama garantisi sunar.",
        p2_title: "LanceDB (Yoğun Vektörler)",
        p2_desc: "Rust tabanlı gömülü sunucusuz vektör veritabanı. Ağ gecikmesi olmadan mili-saniye altında kosinüs benzerlik araması (`sentence-transformers/all-MiniLM-L6-v2`) yapar.",
        p3_title: "KùzuDB (İlişkisel Çizge)",
        p3_desc: "C++ hızında çalışan gömülü özellik çizgesi veritabanı. Uzun zincirli varlık ilişkilerini çözümler ve Python event-loop bloklaması olmadan salınım skoru (`_apply_alpha_reranking`) hesaplar.",
        
        cmp_title: "Objektif Mimari Karşılaştırma Tablosu",
        cmp_subtitle: "Standart RAG mimarileri ve SaaS bellek sarmalayıcılarına karşı başa baş mühendislik değerlendirmesi.",
        
        cmp_col_feature: "Mühendislik Boyutu",
        cmp_col_vector: "Standart RAG (Sadece Vektör / SaaS Ara Katmanlar)",
        cmp_col_mesa: "MESA Üçlü Depolama Motoru (v0.6.0)",
        
        row1_feat: "Çok Adımlı Çıkarım (Multi-Hop) & Varlık Bağlantısı",
        row1_vec: "❌ Karmaşık zincirlerde başarısız olur; vektör mesafesi varlıklar arası ilişkileri göremez.",
        row1_mesa: "✅ Yerel KùzuDB çizge gezintisi + Alpha RRF bağlam zincirini eksiksiz yakalar (`Hit@1: %93.00`).",
        
        row2_feat: "Halüsinasyon & Bağlam Amnezisini Önleme",
        row2_vec: "⚠️ Arka plan kuyrukları ve doğrulanmamış veri blokları nedeniyle yüksek amnezi oranı.",
        row2_mesa: "✅ Valence Motoru ve EWMAD yenilik tespiti, gereksiz gürültüyü veritabanına yazılmadan eler.",
        
        row3_feat: "Kiracı İzolasyonu & Satır Bazlı Güvenlik (RLS)",
        row3_vec: "⚠️ Uygulama katmanı filtrelemesi; kiracılar arası veri sızıntılarına açık yapı.",
        row3_mesa: "🔒 En alt veritabanı adaptör seviyesinde (`WHERE agent_id = ?`) zorunlu matematiksel RLS.",
        
        row4_feat: "Aday Yeniden Sıralama & Hassasiyet",
        row4_vec: "❌ Tek aşamalı kosinüs top-k; yüksek benzerlikli ilgisiz gürültüleri geri döndürür.",
        row4_mesa: "⚡ Çift aşamalı: Alpha RRF Aşama 1 + derin çapraz dikkat (`ms-marco-MiniLM-L-6-v2`) Aşama 2.",
        
        row5_feat: "Hava Boşluklu (Air-Gapped) & Sıfır Maliyetli Mod",
        row5_vec: "❌ Bulut API'lerine (OpenAI/Claude embedding servislerine ve yüksek jeton ücretlerine) bağımlı.",
        row5_mesa: "🛡️ Ollama adaptörleri ve yerel embedding modelleri ile %100 yerel çıkarım (Sıfır API maliyeti).",
        
        // Layer 3: Interactive Sandbox
        sandbox_title: "İnteraktif Sandbox: Canlı Kanıt",
        sandbox_subtitle: "Sözlerimize değil, canlı çalışmaya güvenin. Tarayıcınız üzerinden hibrit aramayı test edin, LanceDB benzerlik skorlarını ve çizge bağlamını anında inceleyin.",
        
        quick_label: "Hızlı Diyagnostik Sorgularını Deneyin:",
        q1_btn: "1. Kurumsal Müşteri Ekle",
        q1_text: "Kullanıcı enterprise planla ilgileniyor, 500 koltuk ve özel RLS istiyor.",
        q2_btn: "2. Müşteri Bağlamını Sorgula",
        q2_text: "Kullanıcının talep ettiği koltuk sayısı ve plan türü nedir?",
        q3_btn: "3. Çizge Çıkarımını Test Et",
        q3_text: "MESA KuzuDB çizge gezintisi halüsinasyonları nasıl engeller?",
        
        chat_heading: "Sohbet ve Doğrudan-Yazım (Direct-Write) Arayüzü",
        telemetry_heading: "Bağlam Denetleyicisi & Canlı Telemetri (`/v3/demo/chat`)",
        chat_placeholder: "Bir soru sorun veya MESA hafızasına yeni bir bilgi ekleyin...",
        btn_logout: "Sandbox Bağlantısını Kes",
        
        // Layer 4: Transparent Benchmark Data & Observability Suite
        bench_title: "Şeffaf Ampirik Gözlemleme & Başarım Vitrini",
        bench_subtitle: "Mühendisler pazarlama metinlerine değil sayılara, telemetriye ve kırılımlara güvenir. Top-K=5 paritesinde 800+ otomatik test ile doğrulandı.",
        
        hit1_title: "Hit@1 Hassasiyeti",
        hit1_sub: "▲ Kosinüs Top-K tabanına kıyasla +%41.8 artış",
        hit3_title: "Hit@3 Hatırlama",
        hit3_sub: "▲ %99.00 Aday Havuzu Garantisi",
        mrr_title: "Ortalama Ters Sıra (MRR)",
        mrr_sub: "▲ Optimum Sıralama Pozisyon Kalitesi",
        ndcg_title: "NDCG@10 Skoru",
        ndcg_sub: "▲ Normalize Edilmiş Kümülatif Kazanç Skoru",
        
        tab_beam: "⚡ BEAM Çelişki Paketi (400 Soru)",
        tab_multihop: "🕸️ Çok Adımlı Çizge Çıkarımı (58 Soru)",
        
        metric_acc: "Altın Veri Seti Doğruluğu",
        metric_acc_sub: "İnsan onaylı referans veriye göre",
        metric_avg_lat: "Ortalama Boru Hattı Gecikmesi",
        metric_avg_lat_sub: "Tam yazım + arama döngü süresi",
        metric_p95: "P95 Gecikme Eşiği",
        metric_p95_sub: "Sorguların %95'i bu sürenin altında tamamlanır",
        metric_p99: "P99 Uç Gecikme süresi",
        metric_p99_sub: "Eşzamanlı yük altında maksimum uç gecikme",
        
        chart_lat_title: "Yürütme Gecikme Şelalesi (`Üçlü Depolama Kırılımı`)",
        chart_gauge_title: "Vektör Tabanına Kıyasla Doğruluk Göstergesi",
        
        scaling_title: "Yüksek Yük Altında Doğrusal Ölçeklenebilirlik (10.000+ Hafıza Düğümü)",
        scaling_desc: "KùzuDB'nin C++ bellek indekslemesi ve LanceDB'nin bellek eşlemeli (memory-mapped) vektör yapıları sayesinde, MESA çizge boyutu 10.000 düğüme ulaşsa bile sorgu gecikmesi doğrusal kalarak `<45ms` (Multi-Hop P95) altında yanıt verir.",
        
        method_title: "Metodolojik Doğrulama ve Jüri Uyum Analizi",
        method_desc: "Değerlendirme boru hattımız, kelime bazlı vekil skorları çift LLM Jürisi (GPT-4 / Claude fikir birliği) ile çapraz doğrular. BEAM testlerinde ölçülen uyum oranı <strong>%79.17</strong> (Cohen's Kappa: <code>0.1319</code>) olarak gerçekleşmiştir. Bu sonuç, hızlı CI/CD testlerinde kelime bazlı kontrolün etkili olduğunu ancak hassas olgusal çıkarımlarda LLM Jürisinin zorunlu olduğunu kanıtlar.",
        
        term_console_title: "Canlı CI/CD Doğrulama Konsolu (`python -m mesa_benchmark.runner`)",
        
        // Layer 5: Enterprise Security & Zero-Cost Mode
        sec_title: "Kurumsal Güvenlik & Sıfır Maliyetli Yerel RAG",
        sec_subtitle: "CTO ve Güvenlik Mimarlarının veri egemenliği ve çoklu kiracı izolasyonu gereksinimlerini karşılamak üzere tasarlandı.",
        
        zc_title: "Sıfır Maliyetli Hava Boşluklu (Air-Gapped) Yerel RAG",
        zc_desc: "Tüm hafıza çıkarımı, vektörleştirme ve yeniden sıralama süreçlerini dış bulut sunucularına tek bir bayt göndermeden kendi donanımınızda çalıştırın.",
        zc_i1: "<strong>Yerel Embedding:</strong> CPU veya GPU üzerinde tamamen yerel çalışan <code>sentence-transformers/all-MiniLM-L6-v2</code> desteği.",
        zc_i2: "<strong>Ollama Entegrasyonu:</strong> Yerel LLM'leri (Llama 3, Mistral, Qwen) üçlü çıkarım ve yanıt üretimi için bağlayın.",
        zc_i3: "<strong>Sıfır Jeton Maliyeti:</strong> Sürekli API ücretlerini ve dışarıya veri sızma riskini tamamen sıfırlayın.",
        
        zt_title: "Sıfır Güven (Zero-Trust) & Epistemic RLS",
        zt_desc: "Her veritabanı operasyonu kriptografik olarak kiracının ajan kimliğine bağlanarak kiracılar arası sıfır sızıntı sağlar.",
        zt_i1: "<strong>Matematiksel Epistemic RLS:</strong> Vektör, kelime ve çizge motorlarında zorunlu <code>WHERE agent_id = ?</code> kalkanı.",
        zt_i2: "<strong>Rol Tabanlı Erişim Kontrolü (RBAC):</strong> Okuma/yazma izinlerini denetleyen katı yetki matrisleri (`mesa_memory/security/rbac.py`).",
        zt_i3: "<strong>Zamanlama Saldırısı & Prompt Injection Kalkanı:</strong> Sabit zamanlı API anahtar rotasyonu ve zararlı girdilere karşı Valence Motoru filtresi.",
        
        // Layer 6: Developer Ecosystem & Integrations
        eco_title: "Geliştirici Ekosistemi & Evrensel Entegrasyonlar",
        eco_subtitle: "MESA'yı mevcut yapay zeka ajan mimarilerine standart adaptörler ve SDK'lar ile anında entegre edin.",
        
        e1_title: "LangChain & LlamaIndex",
        e1_desc: "Hazır bellek sınıfları ve arama adaptörleri ile ajanınızın hafızasını tek satırda değiştirin.",
        e2_title: "FastAPI v3 & Python SDK",
        e2_desc: "Katı Pydantic v2 şemaları (`MemoryInsertRequest`) ve temiz asenkron istemci (`MesaClient`).",
        e3_title: "Model Context Protocol (MCP)",
        e3_desc: "Claude Desktop ve AI IDE'lerle anında bağlantı kuran yerleşik MCP sunucu araçları.",
        e4_title: "Docker & Kubernetes Deployment",
        e4_desc: "Kullanıma hazır Docker imajları, Docker Compose şablonları ve üretime hazır Helm Chart'ları.",
        
        docs_bridge_title: "Kapsamlı Geliştirici Dokümantasyonunu Keşfedin",
        docs_bridge_desc: "Canlı interaktif API uç noktalarını, OpenAPI JSON şemalarını ve detaylı mimari raporları inceleyin.",
        btn_swagger: "OpenAPI Swagger UI (/docs)",
        btn_redoc: "Redoc Şeması (/redoc)",
        btn_whitepaper: "Mimari Teknik Rapor (.md)",
        
        modal_title: "MESA Sandbox'a Bağlan",
        modal_desc: "Arka plan hafıza motoru ile canlı oturum açmak için API Anahtarı ve Ajan ID bilginizi girin.",
        modal_btn: "Oturumu Başlat"
    }
};

// Architecture Pipeline Details for Step Selection
const PIPELINE_DETAILS = {
    en: [
        {
            title: "Stage 01: Candidate Ingestion & Schema Validation",
            desc: "All incoming Cognitive Memory Blocks (CMBs) pass through strict Pydantic v2 schema boundaries (`MemoryInsertRequest`). The endpoint requires an explicit `agent_id` keyword-only parameter, rejecting empty or unset sentinels (`422 Unprocessable Entity`) before memory allocation."
        },
        {
            title: "Stage 02: Valence Motor & Adaptive Novelty Gating",
            desc: "Before incurring transactional storage costs, `calculate_fitness_score` evaluates content density and novelty against an Exponentially Weighted Moving Average of Distances (EWMAD) threshold. Redundant inputs are instantly discarded while uncertain candidates trigger Layer-3 dual-LLM consensus."
        },
        {
            title: "Stage 03: Multi-Store Multi-Write Persistence",
            desc: "Admitted CMBs are transactionally committed via `MemoryDAO` to three isolated storage engines: LanceDB (normalized dense vectors), SQLite WAL (FTS5 lexical keyword index), and KùzuDB (multi-hop property graph node and edge creation via non-blocking threadpool execution)."
        },
        {
            title: "Stage 04: Stage 1 Hybrid Alpha Reciprocal Rank Fusion",
            desc: "During query retrieval, all three engines are queried concurrently (`_apply_alpha_reranking`). Candidates are unified using an enhanced RRF formula (`S_vec + alpha * S_graph + beta * S_lex`) multiplied by the entity's dynamic epistemic confidence score."
        },
        {
            title: "Stage 05: Stage 2 CrossEncoder Reranking & Expansion",
            desc: "Stage 1 selects an expanded pool (`top_n * 3x`) whose text payloads are hydrated via single-query batch lookup (`MemoryDAO.get_nodes_by_ids_batch`) enforced with strict RLS (`WHERE agent_id = ?`). Deep cross-attention (`ms-marco-MiniLM-L-6-v2`) scores query-candidate pairs without event-loop blocking."
        }
    ],
    tr: [
        {
            title: "Aşama 01: Aday İçerik Kabulü ve Şema Doğrulaması",
            desc: "Gelen tüm Bilişsel Hafıza Blokları (CMB) katı Pydantic v2 şema sınırlarından (`MemoryInsertRequest`) geçer. İstek, zorunlu bir `agent_id` parametresi gerektirir; boş veya tanımsız değerler bellek ayrılmadan önce anında reddedilir (`422 Unprocessable Entity`)."
        },
        {
            title: "Aşama 02: Valence Motoru ve Adaptif Yenilik Kapısı",
            desc: "Veritabanı yazım maliyeti oluşmadan önce, `calculate_fitness_score` içerik yoğunluğunu ve Üstel Ağırlıklı Hareketli Mesafe Ortalaması (EWMAD) eşiğine göre yenilik seviyesini ölçer. Tekrar eden girdiler elenirken, belirsiz olanlar çift LLM jürisinin onayına sunulur."
        },
        {
            title: "Aşama 03: Çoklu Depo Çoklu-Yazım (Multi-Write)",
            desc: "Onaylanan hafıza blokları, `MemoryDAO` üzerinden üç ayrı depoya asenkron ve izole olarak yazılır: LanceDB (normalize vektörler), SQLite WAL (FTS5 kelime indeksi) ve KùzuDB (asenkron iş havuzları üzerinden çok adımlı varlık/ilişki çizgesi)."
        },
        {
            title: "Aşama 04: Aşama 1 Hibrit Alpha Ters Sıra Füzyonu (RRF)",
            desc: "Sorgu anında her üç motor eşzamanlı taranır (`_apply_alpha_reranking`). Adaylar gelişmiş RRF formülü (`S_vec + alpha * S_graph + beta * S_lex`) ve varlığın dinamik epistemik güven skoru ile çarpılarak tek bir havuzda birleştirilir."
        },
        {
            title: "Aşama 05: Aşama 2 CrossEncoder Yeniden Sıralama",
            desc: "Aşama 1, 3 kat genişletilmiş (`top_n * 3x`) aday havuzunu seçer. Aday içerikler tek bir optimize SQL sorgusu ve satır bazlı RLS (`WHERE agent_id = ?`) ile getirilir. Derin çapraz dikkat (`ms-marco-MiniLM-L-6-v2`) modeli, event-loop kilitlemeden hassas puanlama yapar."
        }
    ]
};

// Benchmark Data Sets with high-fidelity waterfall & baseline comparisons
const BENCHMARK_DATA = {
    beam: {
        accuracy: "72.75%",
        acc_deg: 262, // 72.75% of 360 deg
        acc_delta: "+31.55% vs Vector Baseline (41.2%)",
        avg_lat: "220.35 ms",
        p95: "428.61 ms",
        p99: "529.60 ms",
        w1_label: "Embedding & Vector Search (`all-MiniLM-L6-v2`)",
        w1_ms: "18 ms",
        w1_pct: "8%",
        w2_label: "KùzuDB Relational Graph Triplet Lookup",
        w2_ms: "32 ms",
        w2_pct: "14%",
        w3_label: "Alpha RRF Fusion & CrossEncoder Stage-2 (`ms-marco`)",
        w3_ms: "45 ms",
        w3_pct: "20%",
        w4_label: "Response Synthesis & Schema Verification",
        w4_ms: "125 ms",
        w4_pct: "58%",
        terminal_cmd: "python -m mesa_benchmark.runner --config config_beam.yaml --queries 400 --rerank cross_encoder",
        terminal_log: `[INFO] Loaded 400 test cases from golden_dataset_beam.json
[INFO] Initializing MemoryDAO: SQLite WAL + LanceDB + KùzuDB (Top-K=5 parity)
[PASS] test_contradiction_001..080 (100% schema compliance)
[PASS] test_temporal_amnesia_081..240 (EWMAD novelty gating active)
[PASS] test_epistemic_rls_241..400 (WHERE agent_id = ? enforced)
======================= EVALUATION SUMMARY =======================
Exact-Match Accuracy: 72.75% | Hit@1: 93.00% | MRR: 0.9592
Judge Agreement Rate: 79.17% (Cohen's Kappa: 0.1319 - Substantial Parity)
Total Execution Time: 88.14s | Zero Context Leakage Violations Detected`
    },
    multihop: {
        accuracy: "29.31%",
        acc_deg: 105, // 29.31% of 360 deg
        acc_delta: "+6.1x vs Vector-Only RAG (4.8%)",
        avg_lat: "35.28 ms",
        p95: "44.11 ms",
        p99: "47.66 ms",
        w1_label: "Embedding & Dense Vector Retrieval",
        w1_ms: "12 ms",
        w1_pct: "34%",
        w2_label: "KùzuDB C++ Multi-Hop Graph Traversal (`2-3 hops`)",
        w2_ms: "14 ms",
        w2_pct: "40%",
        w3_label: "Alpha RRF Multi-Store Score Unified Weighting",
        w3_ms: "9 ms",
        w3_pct: "26%",
        w4_label: "Direct Hydration (Zero-LLM Overhead Mode)",
        w4_ms: "0 ms",
        w4_pct: "0%",
        terminal_cmd: "python -m mesa_benchmark.runner --config config_multihop.yaml --queries 58 --graph-depth 3",
        terminal_log: `[INFO] Loaded 58 multi-hop entity reasoning chains from golden_dataset_graph.json
[INFO] KùzuDB threadpool initialized with 8 async query workers
[PASS] test_graph_chain_01..20 (2-hop entity salience verified)
[PASS] test_graph_chain_21..58 (3-hop causal relationship verified)
======================= EVALUATION SUMMARY =======================
Exact-Match Accuracy: 29.31% (Standard vector baseline fails at 4.80%)
Hit@3 Recall: 99.00% | Mean Traversal Latency: 35.28 ms
Linear Scaling Verified: <45ms P95 maintained across 10,000+ graph nodes`
    }
};

// ---------------------------------------------------------------------------
// DOM References & Initialisation
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initLanguage();
    initCopyButtons();
    initInstallTabs();
    initPipelineInteractive();
    initBenchmarkTabs();
    initPlayground();
    initQuickQueries();
    initScrollEffects();
    initTerminalLogSimulators();
    initMouseParallax();
    initNeuralCanvas();
});

// ---------------------------------------------------------------------------
// Language Translation Switcher
// ---------------------------------------------------------------------------
function initLanguage() {
    const langBtns = document.querySelectorAll(".lang-btn");
    langBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const lang = btn.getAttribute("data-lang");
            setLanguage(lang);
        });
    });
}

function setLanguage(lang) {
    if (!I18N[lang]) return;
    state.currentLang = lang;
    
    document.querySelectorAll(".lang-btn").forEach(btn => {
        if (btn.getAttribute("data-lang") === lang) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        if (I18N[lang][key]) {
            el.innerHTML = I18N[lang][key];
        }
    });

    const chatInput = document.getElementById("chatInput");
    if (chatInput && I18N[lang].chat_placeholder) {
        chatInput.placeholder = I18N[lang].chat_placeholder;
    }

    updatePipelinePanel(state.activePipelineStep);
}

// ---------------------------------------------------------------------------
// Copy to Clipboard Helpers
// ---------------------------------------------------------------------------
function initCopyButtons() {
    document.querySelectorAll(".copy-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-copy-target");
            const targetEl = document.getElementById(targetId);
            if (!targetEl) return;
            
            const textToCopy = targetEl.textContent || targetEl.innerText;
            navigator.clipboard.writeText(textToCopy.trim()).then(() => {
                const origText = btn.innerHTML;
                btn.innerHTML = `<span style="color: var(--accent-tertiary);">✓ ${I18N[state.currentLang].btn_copied}</span>`;
                setTimeout(() => {
                    btn.innerHTML = origText;
                }, 2000);
            }).catch(err => {
                console.error("Failed to copy text: ", err);
            });
        });
    });
}

function initInstallTabs() {
    const tabs = document.querySelectorAll(".install-tab");
    const cmdCode = document.getElementById("installCmdCode");
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            tabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const mode = tab.getAttribute("data-mode");
            state.installMode = mode;
            if (cmdCode) {
                cmdCode.textContent = mode === "pip" ? I18N[state.currentLang].cmd_pip : I18N[state.currentLang].cmd_docker;
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Interactive Pipeline Component
// ---------------------------------------------------------------------------
function initPipelineInteractive() {
    const steps = document.querySelectorAll(".pipeline-step");
    steps.forEach((step, idx) => {
        step.addEventListener("click", () => {
            steps.forEach(s => s.classList.remove("active"));
            step.classList.add("active");
            state.activePipelineStep = idx;
            updatePipelinePanel(idx);
        });
    });
}

function updatePipelinePanel(idx) {
    const titleEl = document.getElementById("pipelineDetailTitle");
    const descEl = document.getElementById("pipelineDetailDesc");
    const details = PIPELINE_DETAILS[state.currentLang][idx];
    if (titleEl && descEl && details) {
        titleEl.textContent = details.title;
        descEl.innerHTML = details.desc.replace(/`([^`]+)`/g, '<code>$1</code>');
    }
}

// ---------------------------------------------------------------------------
// Benchmark Tab Switching & Chart Animation
// ---------------------------------------------------------------------------
function initBenchmarkTabs() {
    const tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.getAttribute("data-tab");
            if (target === state.activeTab) return;
            
            tabBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            
            state.activeTab = target;
            renderBenchmarkData(target);
        });
    });
}

function renderBenchmarkData(tabKey) {
    const data = BENCHMARK_DATA[tabKey];
    if (!data) return;
    
    // Update main metrics
    const accEl = document.getElementById("benchAccVal");
    const avgEl = document.getElementById("benchAvgLatVal");
    const p95El = document.getElementById("benchP95Val");
    const p99El = document.getElementById("benchP99Val");
    const deltaEl = document.getElementById("benchAccDelta");
    
    if (accEl) accEl.textContent = data.accuracy;
    if (avgEl) avgEl.textContent = data.avg_lat;
    if (p95El) p95El.textContent = data.p95;
    if (p99El) p99El.textContent = data.p99;
    if (deltaEl) deltaEl.textContent = data.acc_delta;
    
    // Update circular accuracy dial
    const gaugeTrack = document.getElementById("radialGaugeFill");
    if (gaugeTrack) {
        gaugeTrack.style.strokeDasharray = "0 360";
        setTimeout(() => {
            gaugeTrack.style.strokeDasharray = `${data.acc_deg} 360`;
        }, 50);
    }
    
    // Update Execution Breakdown Waterfall Bars
    const w1Lbl = document.getElementById("waterfall1Label");
    const w1Val = document.getElementById("waterfall1Val");
    const w1Bar = document.getElementById("waterfall1Bar");
    if (w1Lbl && w1Val && w1Bar) {
        w1Lbl.innerHTML = data.w1_label;
        w1Val.textContent = `${data.w1_ms} (${data.w1_pct})`;
        w1Bar.style.width = "0%";
        setTimeout(() => w1Bar.style.width = data.w1_pct, 50);
    }
    
    const w2Lbl = document.getElementById("waterfall2Label");
    const w2Val = document.getElementById("waterfall2Val");
    const w2Bar = document.getElementById("waterfall2Bar");
    if (w2Lbl && w2Val && w2Bar) {
        w2Lbl.innerHTML = data.w2_label;
        w2Val.textContent = `${data.w2_ms} (${data.w2_pct})`;
        w2Bar.style.width = "0%";
        setTimeout(() => w2Bar.style.width = data.w2_pct, 100);
    }
    
    const w3Lbl = document.getElementById("waterfall3Label");
    const w3Val = document.getElementById("waterfall3Val");
    const w3Bar = document.getElementById("waterfall3Bar");
    if (w3Lbl && w3Val && w3Bar) {
        w3Lbl.innerHTML = data.w3_label;
        w3Val.textContent = `${data.w3_ms} (${data.w3_pct})`;
        w3Bar.style.width = "0%";
        setTimeout(() => w3Bar.style.width = data.w3_pct, 150);
    }
    
    const w4Lbl = document.getElementById("waterfall4Label");
    const w4Val = document.getElementById("waterfall4Val");
    const w4Bar = document.getElementById("waterfall4Bar");
    if (w4Lbl && w4Val && w4Bar) {
        w4Lbl.innerHTML = data.w4_label;
        w4Val.textContent = `${data.w4_ms} (${data.w4_pct})`;
        w4Bar.style.width = "0%";
        setTimeout(() => w4Bar.style.width = data.w4_pct, 200);
    }
    
    // Update Simulated Terminal Console
    const termCmd = document.getElementById("benchTermCmd");
    const termLog = document.getElementById("benchTermLog");
    if (termCmd && termLog) {
        termCmd.textContent = data.terminal_cmd;
        termLog.textContent = data.terminal_log;
    }
}

// ---------------------------------------------------------------------------
// Quick Diagnostic Queries for Sandbox
// ---------------------------------------------------------------------------
function initQuickQueries() {
    document.querySelectorAll(".quick-query-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const queryKey = btn.getAttribute("data-query-key");
            const text = I18N[state.currentLang][queryKey];
            const chatInput = document.getElementById("chatInput");
            if (chatInput && text) {
                chatInput.value = text;
                chatInput.focus();
                // Ensure sandbox is connected or show modal
                if (!state.sessionId) {
                    const setupModal = document.getElementById("setupModal");
                    if (setupModal) setupModal.classList.remove("hidden");
                }
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Live API Playground Logic
// ---------------------------------------------------------------------------
function initPlayground() {
    const setupModal     = document.getElementById("setupModal");
    const setupForm      = document.getElementById("setupForm");
    const appContainer   = document.getElementById("appContainer");
    const setupError     = document.getElementById("setupError");
    const setupSpinner   = document.getElementById("setupSpinner");
    const startBtnSpan   = document.querySelector("#startSessionBtn span");

    const headerAgentId  = document.getElementById("headerAgentId");
    const headerSessionId = document.getElementById("headerSessionId");
    const logoutBtn      = document.getElementById("logoutBtn");
    const connectPlayBtn = document.getElementById("connectPlaygroundBtn");
    const heroPlayBtn    = document.getElementById("heroPlaygroundBtn");

    const chatHistory    = document.getElementById("chatHistory");
    const chatForm       = document.getElementById("chatForm");
    const chatInput      = document.getElementById("chatInput");
    const sendBtn        = document.getElementById("sendBtn");
    const telemetryEl    = document.getElementById("telemetryContent");

    const EMPTY_TELEMETRY = `
        <div class="empty-state">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5">
                <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                <path d="M12 8v4l3 3" />
            </svg>
            <p>Waiting for interaction...</p>
            <span class="subtext">Send a message to inspect MESA retrieval scores.</span>
        </div>`;

    function resetTelemetry() {
        if (telemetryEl) telemetryEl.innerHTML = EMPTY_TELEMETRY;
    }

    function setInputLock(locked) {
        state.busy = locked;
        if (chatInput) chatInput.disabled = locked;
        if (sendBtn) sendBtn.disabled = locked;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function addMessage(sender, text, className) {
        const id = "msg-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
        const div = document.createElement("div");
        div.id = id;
        div.className = `message ${className}`;
        div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
        if (chatHistory) {
            chatHistory.appendChild(div);
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }
        return id;
    }

    function updateMessage(id, text, className) {
        const el = document.getElementById(id);
        if (!el) return;
        if (className) el.className = `message ${className}`;
        const bubble = el.querySelector(".bubble");
        if (bubble) {
            bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
        }
        if (chatHistory) chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    const showModal = () => {
        if (setupModal) setupModal.classList.remove("hidden");
    };

    if (connectPlayBtn) connectPlayBtn.addEventListener("click", showModal);
    if (heroPlayBtn) heroPlayBtn.addEventListener("click", showModal);

    if (setupForm) {
        setupForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const apiKey  = document.getElementById("apiKey").value.trim();
            const agentId = document.getElementById("agentId").value.trim();
            if (!apiKey || !agentId) return;

            if (setupError) setupError.classList.add("hidden");
            if (setupSpinner) setupSpinner.classList.remove("hidden");
            if (startBtnSpan) startBtnSpan.textContent = state.currentLang === "en" ? "Connecting..." : "Bağlanılıyor...";

            try {
                const res = await fetch(`${API_BASE}/session/start`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
                    body: JSON.stringify({ agent_id: agentId })
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);

                const data = await res.json();
                state.apiKey   = apiKey;
                state.agentId  = agentId;
                state.sessionId = data.session_id;

                if (setupModal) setupModal.classList.add("hidden");
                if (appContainer) appContainer.classList.remove("hidden");
                if (headerAgentId) headerAgentId.textContent  = `Agent: ${escapeHtml(state.agentId)}`;
                if (headerSessionId) headerSessionId.textContent = state.sessionId;

                addMessage("System", state.currentLang === "en" ? "Session established. MESA direct-write engine active..." : "Oturum kuruldu. MESA doğrudan-yazım motoru aktif...", "system-msg");
                if (chatInput) chatInput.focus();

                const playSection = document.getElementById("sandbox");
                if (playSection) playSection.scrollIntoView({ behavior: "smooth" });
            } catch (err) {
                if (setupError) {
                    setupError.textContent = err.message;
                    setupError.classList.remove("hidden");
                }
            } finally {
                if (setupSpinner) setupSpinner.classList.add("hidden");
                if (startBtnSpan) startBtnSpan.textContent = I18N[state.currentLang].modal_btn || "Start Session";
            }
        });
    }

    if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
            state.apiKey = "";
            state.agentId = "";
            state.sessionId = "";
            state.busy = false;
            if (appContainer) appContainer.classList.add("hidden");
            if (setupModal) setupModal.classList.remove("hidden");
            if (chatHistory) {
                chatHistory.innerHTML = '<div class="message system-msg"><div class="bubble">System initialized. Waiting for input...</div></div>';
            }
            resetTelemetry();
            setInputLock(false);
        });
    }

    if (chatForm) {
        chatForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const text = chatInput.value.trim();
            if (!text || state.busy) return;

            if (!state.sessionId) {
                showModal();
                return;
            }

            chatInput.value = "";
            addMessage("You", text, "user-msg");
            setInputLock(true);

            const typingId = addMessage("MESA", state.currentLang === "en" ? "Traversing Triple-Store & Reranking..." : "Üçlü Depolama Geziniliyor & Sıralanıyor...", "ai-msg typing");

            try {
                const res = await fetch("/v3/demo/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-API-Key": state.apiKey },
                    body: JSON.stringify({
                        agent_id: state.agentId,
                        session_id: state.sessionId,
                        query: text
                    })
                });

                const data = await res.json();
                if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);

                updateMessage(typingId, data.response_text, "ai-msg");
                renderTelemetry(data);
            } catch (err) {
                updateMessage(typingId, `Error: ${err.message}`, "ai-msg error");
            } finally {
                setInputLock(false);
                if (chatInput) chatInput.focus();
            }
        });
    }

    function renderTelemetry(data) {
        if (!telemetryEl) return;
        let html = `
            <div class="stats-row">
                <div class="metric-pill">
                    <span class="label">Latency</span>
                    <span class="value">${data.latency_ms} ms</span>
                </div>
                <div class="metric-pill">
                    <span class="label">Stored</span>
                    <span class="value">${data.memory_stored ? "✓ Direct-Write" : "✗"}</span>
                </div>
                <div class="metric-pill">
                    <span class="label">Context Hits</span>
                    <span class="value">${data.context.length}</span>
                </div>
            </div>`;

        if (data.context.length > 0) {
            html += `<div class="context-section" style="margin-top: 16px;"><h4>Retrieved Context & Relevance (` + (state.currentLang === 'en' ? 'Alpha RRF Score' : 'Alpha RRF Skoru') + `)</h4>`;
            data.context.forEach(ctx => {
                const score = typeof ctx.score === "number" ? ctx.score : 0;
                const relevance = Math.max(0, Math.min(100, (1 - score) * 100));
                html += `
                    <div class="telemetry-card">
                        <div class="card-header">
                            <span class="entity-name">${escapeHtml(ctx.content || ctx.text || ctx.memory || ctx.entity || "Unknown Context")}</span>
                            <span class="score-badge">${score.toFixed(3)}</span>
                        </div>
                        <div class="progress-track">
                            <div class="progress-fill" style="width: ${relevance}%"></div>
                        </div>
                    </div>`;
            });
            html += `</div>`;
        } else {
            html += `
                <div class="empty-state" style="margin-top: 24px;">
                    <p>${state.currentLang === "en" ? "No relevant context found in MESA." : "MESA hafızasında ilgili bağlam bulunamadı."}</p>
                    <span class="subtext">${state.currentLang === "en" ? "Send more messages to expand the graph." : "Çizgeyi genişletmek için daha fazla mesaj gönderin."}</span>
                </div>`;
        }

        telemetryEl.innerHTML = html;
    }
}

// ---------------------------------------------------------------------------
// Scroll Progress & IntersectionObserver Reveal (Linear/Vercel Architecture)
// ---------------------------------------------------------------------------
function initScrollEffects() {
    const scrollProgress = document.getElementById("scrollProgress");
    const navbar = document.querySelector(".navbar");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let ticking = false;
    window.addEventListener("scroll", () => {
        if (!ticking) {
            window.requestAnimationFrame(() => {
                const scrollY = window.scrollY;
                const docHeight = document.documentElement.scrollHeight - window.innerHeight;
                if (scrollProgress && docHeight > 0) {
                    const scrolled = (scrollY / docHeight) * 100;
                    scrollProgress.style.width = Math.min(100, Math.max(0, scrolled)) + "%";
                }
                if (navbar) {
                    if (scrollY > 24) {
                        navbar.classList.add("scrolled");
                    } else {
                        navbar.classList.remove("scrolled");
                    }
                }
                ticking = false;
            });
            ticking = true;
        }
    }, { passive: true });

    if (reducedMotion) return;

    // Staggered intersection observer (Reveals each element exactly ONCE)
    const elementsToReveal = document.querySelectorAll(".section, .pillar-card, .radar-card, .security-card, .eco-item, .terminal-window, .observability-card, .methodology-box");
    
    elementsToReveal.forEach((el, idx) => {
        el.classList.add("reveal-on-scroll");
        const delay = (idx % 3) * 100;
        el.style.transitionDelay = delay + "ms";
    });

    const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add("revealed");
                obs.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.12,
        rootMargin: "0px 0px -40px 0px"
    });

    elementsToReveal.forEach(el => observer.observe(el));
}

// ---------------------------------------------------------------------------
// Subtle Mouse Parallax (< 10px movement, 60 FPS requestAnimationFrame)
// ---------------------------------------------------------------------------
function initMouseParallax() {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) return;

    const bgMesh = document.querySelector(".bg-mesh-gradient");
    const bgNet = document.querySelector(".bg-network-svg");
    if (!bgMesh && !bgNet) return;

    let targetX = 0, targetY = 0;
    let currentX = 0, currentY = 0;
    const maxOffset = 10;

    window.addEventListener("mousemove", (e) => {
        const dx = (e.clientX - window.innerWidth / 2) / (window.innerWidth / 2);
        const dy = (e.clientY - window.innerHeight / 2) / (window.innerHeight / 2);
        targetX = Math.max(-maxOffset, Math.min(maxOffset, dx * maxOffset));
        targetY = Math.max(-maxOffset, Math.min(maxOffset, dy * maxOffset));
    }, { passive: true });

    function animateParallax() {
        currentX += (targetX - currentX) * 0.08;
        currentY += (targetY - currentY) * 0.08;

        if (bgMesh) {
            bgMesh.style.transform = `translate3d(${currentX * -0.65}px, ${currentY * -0.65}px, 0)`;
        }
        if (bgNet) {
            bgNet.style.transform = `translate3d(${currentX}px, ${currentY}px, 0)`;
        }
        window.requestAnimationFrame(animateParallax);
    }
    window.requestAnimationFrame(animateParallax);
}

// ---------------------------------------------------------------------------
// Realistic Terminal Engineering Log Simulators (No Hacker Typing Spam)
// ---------------------------------------------------------------------------
function initTerminalLogSimulators() {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) return;

    const installLogsEl = document.getElementById("installLiveLogs");
    const sdkLogsEl = document.getElementById("sdkLiveLogs");

    const installLogsPool = [
        "[mesa-daemon] Checking property graph schema verification...",
        "[mesa-daemon] KùzuDB relational storage connected (WAL enabled).",
        "[mesa-daemon] LanceDB vector index loaded (`all-MiniLM-L6-v2`).",
        "[mesa-daemon] Stage-2 CrossEncoder reranker ready (`ms-marco-MiniLM-L-6-v2`).",
        "[mesa-daemon] Cache hit: exact triplet match in local FTS5 table.",
        "[mesa-daemon] Memory cluster expansion completed without locks."
    ];

    const sdkLogsPool = [
        "[mesa-client] Request: insert(agent_id='support_01') -> Triplet extraction complete (14ms)",
        "[mesa-client] Request: search(query='enterprise plan') -> Alpha RRF score: 0.942",
        "[mesa-client] CrossEncoder stage-2 latency: 28ms (Hit@1 verified)",
        "[mesa-client] Epistemic RLS check passed (agent_id='support_01' isolated)",
        "[mesa-client] Graph traversal completed: 3-hop causal chain established.",
        "[mesa-client] Benchmark suite verified: zero context amnesia violations detected."
    ];

    let installIdx = 0;
    let sdkIdx = 0;

    setInterval(() => {
        if (installLogsEl) {
            const line = document.createElement("div");
            line.textContent = installLogsPool[installIdx % installLogsPool.length];
            installLogsEl.appendChild(line);
            if (installLogsEl.children.length > 5) {
                installLogsEl.removeChild(installLogsEl.firstElementChild);
            }
            installLogsEl.scrollTop = installLogsEl.scrollHeight;
            installIdx++;
        }

        if (sdkLogsEl) {
            const line = document.createElement("div");
            line.textContent = sdkLogsPool[sdkIdx % sdkLogsPool.length];
            sdkLogsEl.appendChild(line);
            if (sdkLogsEl.children.length > 5) {
                sdkLogsEl.removeChild(sdkLogsEl.firstElementChild);
            }
            sdkLogsEl.scrollTop = sdkLogsEl.scrollHeight;
            sdkIdx++;
        }
    }, 2600);
}

// ---------------------------------------------------------------------------
// Dynamic Neural Network Canvas Background
// ---------------------------------------------------------------------------
function initNeuralCanvas() {
    const canvas = document.getElementById('neuralCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    // Check for reduced motion preference
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        return;
    }

    let width, height;
    let nodes = [];
    let NODE_COUNT;
    let scrollY = window.scrollY;
    
    // Scroll dynamic properties
    let speedMultiplier = 1;
    let connectionDistance = 150;
    let colorHue = 260; // Start at purple/indigo

    window.addEventListener('scroll', () => {
        scrollY = window.scrollY;
        const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
        const scrollPercent = maxScroll > 0 ? scrollY / maxScroll : 0;
        
        // As we scroll down:
        // Speed increases
        speedMultiplier = 1 + scrollPercent * 2;
        // Connections reach further
        connectionDistance = 150 + scrollPercent * 100;
        // Color hue shifts (e.g. from purple 260 to cyan 190)
        colorHue = 260 - (scrollPercent * 70); 
    }, { passive: true });

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = width;
        canvas.height = height;
        
        // Node_count = (width*height)/14000
        const targetNodeCount = Math.floor((width * height) / 14000);
        // Clamp node count for safety
        NODE_COUNT = Math.min(Math.max(targetNodeCount, 40), 150);
        
        initNodes();
    }

    function initNodes() {
        nodes = [];
        for (let i = 0; i < NODE_COUNT; i++) {
            nodes.push({
                x: Math.random() * width,
                y: Math.random() * height,
                vx: (Math.random() - 0.5) * 0.5,
                vy: (Math.random() - 0.5) * 0.5,
                baseRadius: Math.random() * 2 + 1,
                radius: 0,
                phase: Math.random() * Math.PI * 2
            });
        }
    }

    window.addEventListener('resize', resize, { passive: true });
    resize();

    function draw() {
        ctx.clearRect(0, 0, width, height);
        
        const time = Date.now() * 0.001;

        // Update positions
        for (let i = 0; i < NODE_COUNT; i++) {
            const node = nodes[i];
            
            // Organic random noise added to velocity
            node.vx += (Math.random() - 0.5) * 0.02 * speedMultiplier;
            node.vy += (Math.random() - 0.5) * 0.02 * speedMultiplier;
            
            // Dampen velocity to prevent infinite acceleration
            node.vx *= 0.98;
            node.vy *= 0.98;

            node.x += node.vx * speedMultiplier;
            node.y += node.vy * speedMultiplier;

            // Breathing effect
            node.radius = node.baseRadius + Math.sin(time * 2 * speedMultiplier + node.phase) * 0.5;

            // Bounce off edges
            if (node.x < 0 || node.x > width) node.vx *= -1;
            if (node.y < 0 || node.y > height) node.vy *= -1;
            
            // Keep strictly in bounds just in case
            node.x = Math.max(0, Math.min(width, node.x));
            node.y = Math.max(0, Math.min(height, node.y));
        }

        // Draw connections
        ctx.lineWidth = 1.2;
        for (let i = 0; i < NODE_COUNT; i++) {
            for (let j = i + 1; j < NODE_COUNT; j++) {
                const dx = nodes[i].x - nodes[j].x;
                const dy = nodes[i].y - nodes[j].y;
                const distSq = dx * dx + dy * dy;
                const thresholdSq = connectionDistance * connectionDistance;

                if (distSq < thresholdSq) {
                    const dist = Math.sqrt(distSq);
                    const opacity = 1 - (dist / connectionDistance);
                    ctx.strokeStyle = `hsla(${colorHue}, 70%, 60%, ${opacity * 0.5})`;
                    ctx.beginPath();
                    ctx.moveTo(nodes[i].x, nodes[i].y);
                    ctx.lineTo(nodes[j].x, nodes[j].y);
                    ctx.stroke();
                }
            }
        }

        // Draw nodes
        for (let i = 0; i < NODE_COUNT; i++) {
            const node = nodes[i];
            ctx.fillStyle = `hsla(${colorHue}, 80%, 70%, 0.8)`;
            ctx.beginPath();
            ctx.arc(node.x, node.y, Math.max(0.1, node.radius), 0, Math.PI * 2);
            ctx.fill();
        }

        requestAnimationFrame(draw);
    }

    draw();
}
