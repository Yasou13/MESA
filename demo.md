# MESA Arayüz ve Yönetim Konsolu Geliştirme Planı

## 1. Projenin Amacı

MESA arayüzü yalnızca bir sohbet ekranı veya tanıtım sitesi olmamalıdır.

Sistemin temel amacı:

* Agent hafızalarını görüntülemek
* Hafızaların nasıl oluşturulduğunu izlemek
* Retrieval sonuçlarını incelemek
* Bir yanıtın hangi hafızaları kullandığını göstermek
* Knowledge graph ilişkilerini görüntülemek
* Memory sistemlerini benchmark sonuçlarıyla karşılaştırmak
* Hataları ve performans sorunlarını analiz etmek

MESA’nın temel ürün tanımı:

> Bir agent’ın neyi hatırladığını, neden hatırladığını ve hangi hafızayı yanıtında kullandığını gösteren gözlemlenebilir hafıza konsolu.

---

# 2. Genel Ürün Yapısı

MESA iki ayrı arayüzden oluşmalıdır:

```text
MESA
├── Landing Page
└── MESA Console
```

## 2.1 Landing Page

Landing Page, MESA’nın tanıtım sayfasıdır.

Amaçları:

* MESA’nın ne yaptığını açıklamak
* Mimariyi göstermek
* Benchmark sonuçlarını sunmak
* Güvenlik özelliklerini anlatmak
* Dokümantasyona yönlendirmek
* MESA Console’u açtırmak

Landing Page, doğrudan sistem yönetimi için kullanılmamalıdır.

## 2.2 MESA Console

MESA Console, sistemin gerçek yönetim ve gözlem arayüzüdür.

Temel işlevleri:

* Agent yönetimi
* Sohbet ve test ortamı
* Memory görüntüleme
* Retrieval analizi
* Knowledge graph görüntüleme
* Benchmark karşılaştırması
* Log ve trace inceleme
* Sistem durumunu izleme

---

# 3. Mevcut Proje Yapısı

Mevcut yapı:

```text
demo/
├── index.html
├── style.css
├── script.js
├── demo_server.py
└── visualizer/
```

Önerilen yapı:

```text
demo/
├── index.html
│
├── assets/
│   ├── css/
│   │   ├── variables.css
│   │   ├── components.css
│   │   └── landing.css
│   │
│   ├── js/
│   │   └── landing.js
│   │
│   └── images/
│
├── console/
│   ├── index.html
│   ├── console.css
│   ├── console.js
│   │
│   ├── components/
│   │   ├── sidebar.js
│   │   ├── inspector.js
│   │   ├── modal.js
│   │   ├── table.js
│   │   └── status-badge.js
│   │
│   ├── pages/
│   │   ├── overview.js
│   │   ├── playground.js
│   │   ├── memories.js
│   │   ├── retrieval.js
│   │   ├── graph.js
│   │   ├── benchmarks.js
│   │   └── logs.js
│   │
│   └── data/
│       ├── mock-memories.json
│       ├── mock-retrieval.json
│       ├── mock-graph.json
│       └── mock-benchmarks.json
│
├── visualizer/
└── demo_server.py
```

---

# 4. Teknoloji Kararı

## İlk Sürüm

İlk çalışan sürüm şu teknolojilerle hazırlanmalıdır:

```text
HTML
CSS
Vanilla JavaScript
Python demo server
JSON mock data
```

İlk aşamada React veya başka bir frontend framework kullanmak zorunlu değildir.

Bunun nedenleri:

* Mevcut kod korunabilir
* Daha hızlı prototip hazırlanabilir
* Tasarım kararları daha hızlı test edilir
* API yapısı netleşmeden büyük bir frontend mimarisi kurulmaz
* Gereksiz yeniden yazım önlenir

## İleri Sürüm

Console büyüdüğünde şu yapıya geçilebilir:

```text
React
Vite
TypeScript
TanStack Query
Zustand
React Router
```

React’e geçiş koşulları:

* Sayfa sayısı 6–7’yi geçtiğinde
* Bileşen tekrarları arttığında
* State yönetimi zorlaştığında
* Agent, conversation ve memory verileri eş zamanlı güncellendiğinde
* Kullanıcı rolleri ve yetkilendirme eklendiğinde
* Gerçek zamanlı event ve websocket desteği gerektiğinde

---

# 5. Console Genel Yerleşimi

MESA Console üç ana bölümden oluşmalıdır:

```text
┌──────────────┬────────────────────────────────┬──────────────────┐
│ Sidebar      │ Ana Çalışma Alanı              │ Inspector        │
│              │                                │                  │
│ Overview     │ Aktif sayfa içeriği            │ Context          │
│ Playground   │                                │ Retrieval        │
│ Memories     │                                │ Metadata         │
│ Retrieval    │                                │ Trace            │
│ Graph        │                                │                  │
│ Benchmarks   │                                │                  │
│ Logs         │                                │                  │
└──────────────┴────────────────────────────────┴──────────────────┘
```

## 5.1 Sidebar

Sidebar sabit olmalıdır.

Menü seçenekleri:

```text
Overview
Playground
Memories
Retrieval
Knowledge Graph
Benchmarks
Logs
Settings
```

Alt bölüm:

* API bağlantı durumu
* Aktif proje
* Aktif agent
* Kullanıcı profili
* Tema seçimi

## 5.2 Ana Çalışma Alanı

Seçilen sayfanın ana içeriği burada gösterilir.

Örnekler:

* Sohbet ekranı
* Memory tablosu
* Retrieval sonuçları
* Knowledge graph
* Benchmark grafikleri

## 5.3 Inspector Paneli

Inspector paneli sağ tarafta açılır.

Her zaman açık olmak zorunda değildir.

Sekmeler:

```text
Details
Context
Retrieval
Metadata
Trace
```

Inspector şu durumlarda açılabilir:

* Bir memory seçildiğinde
* Bir sohbet yanıtı seçildiğinde
* Bir retrieval sonucu seçildiğinde
* Bir graph node seçildiğinde
* Bir log kaydı seçildiğinde

---

# 6. Landing Page Planı

Landing Page sade ve ürün odaklı olmalıdır.

## 6.1 Navbar

```text
Product
Architecture
Benchmarks
Security
Documentation
GitHub
Open Console
```

Ana vurgu butonu:

```text
Open Console
```

## 6.2 Hero Bölümü

Hero alanında yalnızca şunlar bulunmalıdır:

* MESA ürün adı
* Tek cümlelik açıklama
* Ana CTA
* İkincil CTA
* Küçük bir ürün ekranı önizlemesi

Örnek:

```text
Observable Memory Infrastructure for AI Agents

Inspect, evaluate and debug what your agents remember.

[Open Console] [View Documentation]
```

## 6.3 Landing Page Bölümleri

Landing Page şu bölümlerle sınırlandırılmalıdır:

```text
Hero
Problem
MESA Architecture
Interactive Preview
Benchmark Summary
Security
Developer Integration
Final CTA
```

## 6.4 Landing Page’den Kaldırılacaklar

* Tam sohbet uygulaması
* Uzun teknik loglar
* Çok sayıda animasyon
* Gereksiz terminal çıktıları
* Tekrarlanan benchmark kartları
* Fazla uzun açıklamalar
* Aynı bilgiyi tekrar eden bölümler

Landing Page’de yalnızca küçük bir demo önizlemesi bulunmalıdır.

Tam sistem deneyimi Console üzerinden sunulmalıdır.

---

# 7. Overview Sayfası

Overview, sistemin genel durumunu göstermelidir.

## 7.1 Ana Metrikler

Üst bölümde en fazla dört ana kart bulunmalıdır:

```text
Total Memories
Active Agents
Memory Hit Rate
Average Retrieval Latency
```

İsteğe bağlı ikincil metrikler:

```text
Total Entities
Total Relations
Failed Retrievals
Storage Usage
```

## 7.2 Sistem Durumu

Durum göstergeleri:

* MESA API
* PostgreSQL
* Vector database
* Graph database
* Embedding model
* Reranker
* LLM provider

Durum türleri:

```text
Healthy
Degraded
Offline
Unknown
```

## 7.3 Son Aktiviteler

Alt bölüm:

* Son konuşmalar
* Son eklenen hafızalar
* Son retrieval sorguları
* Son benchmark çalışması
* Son sistem hataları

---

# 8. Playground Sayfası

Playground, MESA’nın ana test ortamıdır.

Mevcut sohbet demosu bu sayfaya taşınmalıdır.

## 8.1 Sol Panel

Opsiyonel konuşma listesi:

* Yeni konuşma
* Son konuşmalar
* Agent seçimi
* Kullanıcı seçimi

## 8.2 Orta Alan

Sohbet mesajları:

* Kullanıcı mesajı
* Agent yanıtı
* Tool çağrısı
* Retrieval olayı
* Memory write olayı
* Sistem olayı

Her agent yanıtının altında teknik özet gösterilmelidir:

```text
4 memories used
136 ms retrieval
1,280 tokens
2 memory writes
```

## 8.3 Sağ Inspector

Sekmeler:

```text
Context
Retrieved Memories
Memory Writes
Pipeline Trace
```

### Context

Gösterilecek bilgiler:

* System prompt
* Son konuşma mesajları
* Eklenen memory kayıtları
* Tool çıktıları
* Toplam context token sayısı

### Retrieved Memories

Her memory için:

* Memory özeti
* Final score
* Memory türü
* Kaynak
* Seçildi veya elendi durumu

### Memory Writes

* Yeni oluşturulan memory
* Güncellenen memory
* Atlanan memory
* Duplicate olarak işaretlenen memory

### Pipeline Trace

```text
User Query
→ Query Normalization
→ Vector Search
→ Keyword Search
→ Graph Expansion
→ Reranking
→ Context Selection
→ LLM Response
→ Memory Extraction
→ Memory Write
```

---

# 9. Memory Explorer Sayfası

Memory Explorer, MESA Console’un en önemli ekranlarından biri olmalıdır.

## 9.1 Arama Alanı

```text
Search memories...
```

Arama türleri:

* Semantic search
* Keyword search
* Hybrid search
* Exact match

## 9.2 Filtreler

```text
Agent
User
Project
Memory Type
Source
Date
Importance
Confidence
Status
```

## 9.3 Memory Türleri

```text
Core Memory
Episodic Memory
Semantic Memory
Working Memory
User Profile
Project Memory
Conversation Memory
Document Memory
```

## 9.4 Memory Tablosu

```text
Memory
Type
Source
Importance
Confidence
Last Accessed
Retrieval Count
Status
```

## 9.5 Memory Detay Paneli

Bir memory seçildiğinde gösterilecek bilgiler:

```text
Full Content
Summary
Memory Type
Source Message
Source Conversation
Extracted Entities
Graph Relations
Importance Score
Confidence Score
Retrieval Count
Created At
Updated At
Last Accessed
Version History
Metadata
```

## 9.6 Memory İşlemleri

İlk sürüm:

```text
Create
Edit
Pin
Archive
Delete
Re-embed
```

İleri sürüm:

```text
Merge
Split
Link
Unlink
Change Type
Change Importance
Restore Version
```

---

# 10. Retrieval Inspector Sayfası

Retrieval Inspector, bir sorgunun neden belirli hafızaları seçtiğini göstermelidir.

## 10.1 Sorgu Formu

```text
Query
Agent
User
Namespace
Top-K
Similarity Threshold
Search Mode
Reranking
Graph Depth
```

Search Mode seçenekleri:

```text
Vector
Keyword
Graph
Hybrid
```

## 10.2 Sonuç Tablosu

```text
Rank
Memory
Vector Score
Keyword Score
Graph Score
Recency Score
Importance Score
Final Score
Decision
```

Decision değerleri:

```text
Selected
Rejected
Below Threshold
Duplicate
Filtered
```

## 10.3 Retrieval Pipeline

```text
Query
→ Normalize
→ Embed
→ Vector Search
→ Full-Text Search
→ Graph Expansion
→ Merge Results
→ Rerank
→ Context Selection
```

Her aşamada:

* İşlem süresi
* Girdi kayıt sayısı
* Çıktı kayıt sayısı
* Kullanılan parametreler
* Elenen kayıtlar
* Hata bilgisi

## 10.4 Debug Özellikleri

* Threshold değiştirme
* Top-K değiştirme
* Reranker açma veya kapatma
* Graph retrieval açma veya kapatma
* Sonuçları yeniden çalıştırma
* İki retrieval ayarını karşılaştırma

---

# 11. Knowledge Graph Sayfası

Mevcut visualizer tamamen kaldırılmamalıdır.

Console içindeki Knowledge Graph sayfasına dönüştürülmelidir.

## 11.1 Node Türleri

```text
User
Agent
Project
Conversation
Memory
Entity
Event
Document
Location
Organization
Person
Concept
```

## 11.2 Filtreler

```text
Node Type
Relation Type
Agent
User
Project
Date
Confidence
Depth
```

## 11.3 Graph İşlemleri

* Node arama
* Node seçme
* Node detayını açma
* Bağlantılı memory kayıtlarını gösterme
* Bağlantılı konuşmaları gösterme
* İlişkileri filtreleme
* Graph depth değiştirme
* Node sabitleme
* Graph görünümünü sıfırlama

## 11.4 Node Inspector

```text
Entity Name
Entity Type
Description
Connected Memories
Relations
Confidence
Source
Created At
Updated At
```

## 11.5 Mevcut Visualizer’dan Kaldırılacaklar

* Uzun eğitim metinleri
* Sürekli yönlendirme açıklamaları
* Gereksiz demo mesajları
* Çok büyük animasyonlu arka planlar
* Ürün kullanımını engelleyen efektler

---

# 12. Benchmark Sayfası

Benchmark ekranı, MESA’yı diğer memory sistemleriyle karşılaştırmalıdır.

## 12.1 Desteklenecek Sistemler

```text
MESA
Letta
Mem0
Zep
Cognee
Vector-Only Baseline
Standard RAG
```

## 12.2 Benchmark Metrikleri

```text
Accuracy
Precision
Recall
F1 Score
Hit@1
Hit@5
MRR
Multi-Hop Accuracy
Temporal Accuracy
Long-Term Recall
Average Latency
P95 Latency
Token Usage
Storage Usage
Memory Write Success
Retrieval Success
```

## 12.3 Benchmark Oluşturma Formu

```text
System
Dataset
Model
Embedding Model
Reranker
Scenario
Repeat Count
Concurrency
Timeout
```

## 12.4 Sonuç Ekranı

* Genel sıralama
* Dataset bazlı skor
* Sistem karşılaştırması
* Latency karşılaştırması
* Token kullanımı
* Başarısız sorgular
* Yanlış retrieval örnekleri
* Hata kategorileri

## 12.5 İlk Sürüm Yaklaşımı

İlk sürümde benchmark işlemleri gerçek zamanlı çalışmak zorunda değildir.

Önce mevcut benchmark JSON dosyaları yüklenip görüntülenebilir.

Örnek:

```json
{
  "system": "MESA",
  "dataset": "LongMemEval",
  "accuracy": 0.82,
  "mrr": 0.79,
  "average_latency_ms": 145,
  "token_usage": 1320
}
```

## 12.6 Dışa Aktarma

```text
Export JSON
Export CSV
Generate Markdown Report
Compare Runs
Repeat Benchmark
```

---

# 13. Logs ve Trace Sayfası

## 13.1 Log Türleri

```text
Memory Write
Memory Update
Memory Delete
Retrieval
Embedding
Reranking
Graph Query
Tool Call
API Request
Authentication
Benchmark
System Error
```

## 13.2 Filtreler

```text
Date
Level
Agent
User
Project
Request ID
Operation Type
Status
```

## 13.3 Log Detayı

```text
Request
Response
Duration
Model
Tokens
Status
Error
Stack Trace
Pipeline Steps
Metadata
```

## 13.4 Log Seviyeleri

```text
Debug
Info
Warning
Error
Critical
```

---

# 14. Agent Yönetimi

Agent yönetimi ilk MVP sonrasında eklenebilir.

## 14.1 Agent Listesi

```text
Agent
Model
Memory Profile
Memories
Conversations
Last Active
Status
```

## 14.2 Agent Detay Sekmeleri

```text
Overview
Playground
Memory
Configuration
Tools
Activity
```

## 14.3 Agent Ayarları

```text
System Prompt
Model
Temperature
Context Limit
Retrieval Top-K
Similarity Threshold
Memory Decay
Deduplication
Reranking
Automatic Summarization
Graph Retrieval
```

---

# 15. Data Sources

İleri sürümlerde veri kaynağı yönetimi eklenebilir.

## Desteklenecek Kaynaklar

```text
Markdown
Text
PDF
JSON
CSV
Web Page
GitHub Repository
PostgreSQL
REST API
Conversation Import
```

## Kaynak Bilgileri

```text
Source Name
Type
Status
Document Count
Chunk Count
Last Sync
Error Count
Connected Agents
```

## Kaynak İşlemleri

```text
Upload
Sync
Pause
Re-index
Delete
View Chunks
Edit Metadata
```

---

# 16. API Planı

Mevcut endpoint’ler korunabilir:

```text
POST /v3/memory/session/start
POST /v3/demo/chat
```

Console için gerekli temel endpoint’ler:

## Overview

```text
GET /v3/overview
GET /v3/system/health
```

## Memories

```text
GET    /v3/memories
GET    /v3/memories/:id
POST   /v3/memories
PATCH  /v3/memories/:id
DELETE /v3/memories/:id
POST   /v3/memories/:id/re-embed
```

## Retrieval

```text
POST /v3/retrieval/search
GET  /v3/retrieval/:requestId
GET  /v3/retrieval/:requestId/trace
```

## Conversations

```text
POST /v3/chat
GET  /v3/conversations
GET  /v3/conversations/:id
GET  /v3/conversations/:id/messages
```

## Graph

```text
GET /v3/graph
GET /v3/graph/entities
GET /v3/graph/entities/:id
GET /v3/graph/entities/:id/relations
```

## Benchmarks

```text
GET  /v3/benchmarks
GET  /v3/benchmarks/:id
POST /v3/benchmarks
GET  /v3/benchmarks/:id/results
```

## Logs

```text
GET /v3/logs
GET /v3/logs/:id
```

API endpoint’leri hazır değilse frontend önce mock JSON verileriyle geliştirilmelidir.

---

# 17. Mock Veri Yaklaşımı

Frontend geliştirme backend’den bağımsız ilerleyebilmelidir.

Örnek dosyalar:

```text
mock-memories.json
mock-retrieval.json
mock-conversations.json
mock-graph.json
mock-benchmarks.json
mock-logs.json
```

Örnek memory verisi:

```json
{
  "id": "memory_001",
  "content": "User prefers PostgreSQL for production systems.",
  "summary": "Database preference",
  "type": "user_profile",
  "importance": 0.86,
  "confidence": 0.94,
  "retrieval_count": 12,
  "created_at": "2026-07-20T10:00:00Z",
  "last_accessed": "2026-07-20T13:40:00Z",
  "entities": [
    {
      "name": "PostgreSQL",
      "type": "technology"
    }
  ]
}
```

---

# 18. Görsel Tasarım Sistemi

## 18.1 Tema

MESA için sade ve teknik bir koyu tema kullanılmalıdır.

```css
:root {
  --background: #09090b;
  --sidebar: #0d0d10;
  --surface: #121215;
  --surface-raised: #18181c;
  --surface-hover: #202024;

  --border: #29292f;
  --border-strong: #3f3f46;

  --text-primary: #f4f4f5;
  --text-secondary: #a1a1aa;
  --text-muted: #71717a;

  --accent: #7867e6;
  --accent-hover: #8979ee;

  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;
  --info: #3b82f6;
}
```

## 18.2 Korunacak Tasarım Özellikleri

* Koyu tema
* Mor vurgu rengi
* Monospace teknik değerler
* İnce kenarlıklar
* Pipeline görünümü
* Knowledge graph
* Teknik ve sade görünüm

## 18.3 Azaltılacak Tasarım Özellikleri

* Büyük glow efektleri
* Çoklu gradient kullanımı
* Sürekli hareket eden arka plan
* Mouse parallax efektleri
* Her kartta animasyon
* Her butonda gölge
* Uzun açıklama metinleri
* Fazla emoji
* Yapay terminal çıktıları

## 18.4 Tipografi

Önerilen font türleri:

```text
Arayüz:
Inter
Geist
IBM Plex Sans

Teknik veriler:
JetBrains Mono
IBM Plex Mono
Geist Mono
```

## 18.5 Kart Tasarımı

Kartlar:

* Düz koyu yüzey
* İnce sınır
* Küçük radius
* Hafif hover
* Gereksiz glow olmadan
* Sınırlı animasyonla

---

# 19. Responsive Tasarım

## Desktop

```text
Sidebar + Main Content + Inspector
```

## Tablet

```text
Dar Sidebar + Main Content
Inspector drawer olarak açılır
```

## Mobil

```text
Bottom Navigation
Main Content
Inspector tam ekran modal
```

Mobil ilk MVP için öncelikli değildir.

Öncelik desktop deneyimidir.

---

# 20. Uygulama Aşamaları

## Aşama 1 — Proje Yapısını Ayır

* Landing Page dosyalarını düzenle
* Ortak CSS değişkenlerini oluştur
* `/console` klasörünü oluştur
* Console layout hazırla
* Sidebar oluştur
* Inspector paneli oluştur
* Sayfa yönlendirme sistemini kur

Çıktı:

```text
Çalışan boş MESA Console iskeleti
```

## Aşama 2 — Playground

* Mevcut sohbet demosunu Console’a taşı
* Agent yanıt kartlarını oluştur
* Teknik metrikleri göster
* Context sekmesini oluştur
* Retrieved Memories sekmesini oluştur
* Pipeline Trace sekmesini oluştur
* Mevcut chat API bağlantısını koru

Çıktı:

```text
Çalışan sohbet ve memory gözlemleme ekranı
```

## Aşama 3 — Memory Explorer

* Memory tablosunu oluştur
* Semantic arama alanını ekle
* Filtreleri oluştur
* Memory detail inspector ekle
* Edit işlemi ekle
* Pin işlemi ekle
* Archive işlemi ekle
* Delete işlemi ekle
* Re-embed işlemi ekle

Çıktı:

```text
Hafızaların aranabildiği ve incelenebildiği ekran
```

## Aşama 4 — Retrieval Inspector

* Retrieval sorgu formunu oluştur
* Score tablosunu oluştur
* Pipeline görünümünü oluştur
* Selected ve rejected sonuçları ayır
* Threshold değiştirme özelliği ekle
* Top-K değiştirme özelliği ekle
* Aynı sorguyu yeniden çalıştırma özelliği ekle

Çıktı:

```text
Bir memory sonucunun neden seçildiğini gösteren ekran
```

## Aşama 5 — Knowledge Graph

* Mevcut visualizer’ı Console’a taşı
* Gereksiz eğitim metinlerini kaldır
* Node arama ekle
* Node türü filtresi ekle
* Relation filtresi ekle
* Depth kontrolü ekle
* Node inspector oluştur

Çıktı:

```text
Memory ve entity ilişkilerini gösteren graph ekranı
```

## Aşama 6 — Benchmark

* Benchmark JSON yükleme özelliği ekle
* Sistem karşılaştırma tablosu oluştur
* Metrik kartları oluştur
* Hata örneklerini göster
* CSV dışa aktarma ekle
* JSON dışa aktarma ekle
* Markdown raporu oluşturma ekle

Çıktı:

```text
MESA ile diğer memory sistemlerini karşılaştıran ekran
```

## Aşama 7 — Logs

* Log tablosu oluştur
* Filtreleri ekle
* Log inspector oluştur
* Request ve response görüntüleme ekle
* Pipeline trace göster
* Hata mesajlarını grupla

Çıktı:

```text
Sistem davranışlarının izlenebildiği debug ekranı
```

## Aşama 8 — Landing Page Sadeleştirme

* Hero alanını sadeleştir
* Ana CTA’yı Open Console yap
* Sandbox bölümünü küçük önizlemeye dönüştür
* Gereksiz animasyonları kaldır
* Tekrarlanan açıklamaları sil
* Benchmark bölümünü özet haline getir
* Console’a yönlendirme ekle

Çıktı:

```text
Daha profesyonel ve ürün odaklı tanıtım sayfası
```

---

# 21. MVP Kapsamı

İlk gerçek MVP yalnızca şu modüllerden oluşmalıdır:

```text
1. Console Layout
2. Playground
3. Memory Explorer
4. Retrieval Inspector
5. Knowledge Graph
```

MVP’de zorunlu olmayanlar:

```text
Advanced Dashboard
Agent Management
User Roles
Data Sources
API Key Management
Real-Time Benchmark Runner
Cost Analytics
Deployment Management
Advanced Settings
```

---

# 22. İlk MVP Kullanıcı Akışı

Kullanıcı Console’u açar.

```text
Open Console
→ Playground
→ Agent ile konuş
→ Kullanılan hafızaları görüntüle
→ Bir memory kaydını aç
→ Retrieval skorlarını incele
→ Memory’nin graph ilişkilerini görüntüle
```

Alternatif akış:

```text
Memory Explorer
→ Memory ara
→ Memory seç
→ Kaynak konuşmayı görüntüle
→ İlişkili entity’leri görüntüle
→ Retrieval geçmişini incele
```

Retrieval test akışı:

```text
Retrieval Inspector
→ Sorgu gir
→ Top-K belirle
→ Sonuçları çalıştır
→ Selected ve rejected memory kayıtlarını karşılaştır
→ Pipeline süresini incele
```

---

# 23. Başarı Kriterleri

MVP tamamlandığında kullanıcı şu sorulara arayüz üzerinden cevap verebilmelidir:

* Agent hangi bilgileri hatırlıyor?
* Bu memory ne zaman oluşturuldu?
* Memory hangi konuşmadan geldi?
* Memory neden seçildi?
* Hangi retrieval skoru etkili oldu?
* Hangi memory sonuçtan elendi?
* Agent yanıtında hangi hafızalar kullanıldı?
* Yeni memory oluşturuldu mu?
* Memory hangi entity’lerle bağlantılı?
* Retrieval işlemi ne kadar sürdü?
* MESA diğer sistemlerden daha iyi mi?
* Hangi benchmark sorgularında başarısız oldu?

---

# 24. Öncelik Sırası

## Yüksek Öncelik

```text
Console Layout
Playground
Memory Explorer
Context Inspector
Retrieval Inspector
Knowledge Graph
```

## Orta Öncelik

```text
Overview
Benchmarks
Logs
Agent Management
```

## Düşük Öncelik

```text
Roles and Permissions
Data Sources
API Keys
Webhooks
Cost Analytics
Deployment
Advanced Integrations
```

---

# 25. Kaçınılması Gerekenler

* Her özelliği ilk sürüme eklemek
* Landing Page ile Console’u aynı sayfada tutmak
* Backend hazır olmadan frontend’i bekletmek
* API sözleşmesi netleşmeden React’e geçmek
* Her alanda animasyon kullanmak
* Çok fazla renk kullanmak
* Her metriği dashboard’a yerleştirmek
* Knowledge graph’u yalnızca görsel efekt olarak kullanmak
* Benchmark sonuçlarını açıklamasız grafiklerle göstermek
* Memory ve retrieval işlemlerini kullanıcıdan gizlemek

---

# 26. Nihai Hedef

MESA Console, Letta ADE benzeri bir agent geliştirme ekranının ötesine geçmelidir.

MESA’nın farkı şu özelliklerde olmalıdır:

* Memory observability
* Retrieval explainability
* Context inspection
* Memory lifecycle management
* Graph relationship inspection
* Benchmark comparison
* Failure analysis
* Performance tracing

Nihai ürün şu yapıda olmalıdır:

```text
MESA Console
├── Agent Playground
├── Memory Explorer
├── Retrieval Inspector
├── Context Inspector
├── Knowledge Graph
├── Benchmark Lab
└── Logs and Traces
```

Bu yapı, mevcut demo dosyalarını koruyarak sistemi adım adım gerçek bir memory yönetim ve gözlem platformuna dönüştürür.
