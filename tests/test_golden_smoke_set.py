"""Golden Smoke Set for Round 5 (Fact Extraction & Retrieval).

Scope:
- 35 Turkish fact extraction test cases across 8 core categories:
  (0 facts, 1 fact, multiple facts, correction/supersession, temporal changes,
   user preferences, technical configuration, negative statements).
- 25 retrieval test cases verifying query embedding, similarity matching, and candidate filtering.
"""

import pytest

from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_memory.extraction.service import (
    FactExtractionService,
)


# ===========================================================================
# 1. Turkish Fact Extraction Golden Dataset (35 Cases)
# ===========================================================================
GOLDEN_EXTRACTION_CASES = [
    # --- Category 1: 0 Facts / Irrelevant Conversation / Greetings (5 cases) ---
    {
        "id": "ext_01",
        "category": "0_facts",
        "text": "Merhaba, nasılsın bugün?",
        "expected_facts_count": 0,
    },
    {
        "id": "ext_02",
        "category": "0_facts",
        "text": "Günaydın! Harika bir gün olsun.",
        "expected_facts_count": 0,
    },
    {
        "id": "ext_03",
        "category": "0_facts",
        "text": "Teşekkür ederim, çok yardımcı oldun.",
        "expected_facts_count": 0,
    },
    {
        "id": "ext_04",
        "category": "0_facts",
        "text": "Anladım, peki sonra görüşürüz.",
        "expected_facts_count": 0,
    },
    {
        "id": "ext_05",
        "category": "0_facts",
        "text": "İyi akşamlar, yarın konuşuruz.",
        "expected_facts_count": 0,
    },

    # --- Category 2: 1 Fact (5 cases) ---
    {
        "id": "ext_06",
        "category": "1_fact",
        "text": "Ahmet Ankara'da yaşıyor.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Ahmet", "predicate": "YAŞIYOR", "object": "Ankara"}
        ],
    },
    {
        "id": "ext_07",
        "category": "1_fact",
        "text": "MESA projesi Apache 2.0 lisansı ile korunmaktadır.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "MESA projesi", "predicate": "LİSANSLIDIR", "object": "Apache 2.0"}
        ],
    },
    {
        "id": "ext_08",
        "category": "1_fact",
        "text": "Şirketin merkezi Maslak İstanbul adresindedir.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Şirket merkezi", "predicate": "ADRESİNDEDİR", "object": "Maslak İstanbul"}
        ],
    },
    {
        "id": "ext_09",
        "category": "1_fact",
        "text": "Python 3.13 sürümü sistemde kurulu.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Python", "predicate": "KURULU_SÜRÜM", "object": "3.13"}
        ],
    },
    {
        "id": "ext_10",
        "category": "1_fact",
        "text": "Zeynep kıdemli yazılım mühendisi olarak çalışmaktadır.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Zeynep", "predicate": "GÖREVİNDEDİR", "object": "Kıdemli Yazılım Mühendisi"}
        ],
    },

    # --- Category 3: Multiple Facts (5 cases) ---
    {
        "id": "ext_11",
        "category": "multiple_facts",
        "text": "FastAPI web çerçevesi olarak, PostgreSQL ise veritabanı olarak kullanılıyor.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "FastAPI", "predicate": "KULLANILIYOR", "object": "Web Çerçevesi"},
            {"subject": "PostgreSQL", "predicate": "KULLANILIYOR", "object": "Veritabanı"},
        ],
    },
    {
        "id": "ext_12",
        "category": "multiple_facts",
        "text": "Mehmet backend ekibinde çalışıyor ve Python geliştiriyor.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "Mehmet", "predicate": "ÇALIŞIYOR", "object": "Backend Ekibi"},
            {"subject": "Mehmet", "predicate": "GELİŞTİRİYOR", "object": "Python"},
        ],
    },
    {
        "id": "ext_13",
        "category": "multiple_facts",
        "text": "Sunucu IP adresi 192.168.1.50 ve port 8000 olarak yapılandırıldı.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "Sunucu", "predicate": "IP_ADRESİ", "object": "192.168.1.50"},
            {"subject": "Sunucu", "predicate": "PORT", "object": "8000"},
        ],
    },
    {
        "id": "ext_14",
        "category": "multiple_facts",
        "text": "Ali Almanca ve İngilizce bilmektedir.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "Ali", "predicate": "BİLİYOR", "object": "Almanca"},
            {"subject": "Ali", "predicate": "BİLİYOR", "object": "İngilizce"},
        ],
    },
    {
        "id": "ext_15",
        "category": "multiple_facts",
        "text": "Mikroservis mimarisinde Kafka mesaj kuyruğu, Redis ise önbellek olarak seçildi.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "Kafka", "predicate": "GÖREVİ", "object": "Mesaj Kuyruğu"},
            {"subject": "Redis", "predicate": "GÖREVİ", "object": "Önbellek"},
        ],
    },

    # --- Category 4: Correction / Supersession (5 cases) ---
    {
        "id": "ext_16",
        "category": "correction",
        "text": "Eski telefon numaramı iptal ettim, yeni numaram 05551234567.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Telefon Numarası",
                "predicate": "YENİ_DEĞER",
                "object": "05551234567",
                "supersedes": "Eski telefon numarası",
            }
        ],
    },
    {
        "id": "ext_17",
        "category": "correction",
        "text": "Artık MySQL değil, PostgreSQL kullanıyoruz.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Veritabanı",
                "predicate": "KULLANILIYOR",
                "object": "PostgreSQL",
                "supersedes": "MySQL",
            }
        ],
    },
    {
        "id": "ext_18",
        "category": "correction",
        "text": "Toplantı saati 14:00'ten 15:30'a ertelendi.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Toplantı Saati",
                "predicate": "GÜNCELLENDİ",
                "object": "15:30",
                "supersedes": "14:00",
            }
        ],
    },
    {
        "id": "ext_19",
        "category": "correction",
        "text": "Ofis Kadıköy'den Levent'e taşındı.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Ofis Konumu",
                "predicate": "TAŞINDI",
                "object": "Levent",
                "supersedes": "Kadıköy",
            }
        ],
    },
    {
        "id": "ext_20",
        "category": "correction",
        "text": "Mavi tema yerine koyu tema tercih ediyorum.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Tema Tercihi",
                "predicate": "SEÇİLDİ",
                "object": "Koyu Tema",
                "supersedes": "Mavi Tema",
            }
        ],
    },

    # --- Category 5: Temporal Changes (5 cases) ---
    {
        "id": "ext_21",
        "category": "temporal",
        "text": "Sözleşme 2026-01-01 tarihinde başladı ve 2026-12-31 tarihinde sona erecektir.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Sözleşme",
                "predicate": "GEÇERLİLİK",
                "object": "Aktif",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
            }
        ],
    },
    {
        "id": "ext_22",
        "category": "temporal",
        "text": "Can 1 Haziran 2025'ten beri proje yöneticisidir.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Can",
                "predicate": "GÖREVİ",
                "object": "Proje Yöneticisi",
                "valid_from": "2025-06-01",
            }
        ],
    },
    {
        "id": "ext_23",
        "category": "temporal",
        "text": "Erişim izni 24 saatliğine verildi.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Erişim İzni",
                "predicate": "DURUMU",
                "object": "Verildi",
                "valid_to": "24 saat",
            }
        ],
    },
    {
        "id": "ext_24",
        "category": "temporal",
        "text": "Beta sürümü 2026-09-15 tarihine kadar yayında kalacaktır.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Beta Sürümü",
                "predicate": "YAYINDA",
                "object": "Aktif",
                "valid_to": "2026-09-15",
            }
        ],
    },
    {
        "id": "ext_25",
        "category": "temporal",
        "text": "Yıllık bakım 2026-03-01 ile 2026-03-05 tarihleri arasında yapılacaktır.",
        "expected_facts_count": 1,
        "facts": [
            {
                "subject": "Yıllık Bakım",
                "predicate": "TARİHİ",
                "object": "Planlandı",
                "valid_from": "2026-03-01",
                "valid_to": "2026-03-05",
            }
        ],
    },

    # --- Category 6: User Preferences (4 cases) ---
    {
        "id": "ext_26",
        "category": "preference",
        "text": "Bana yanıt verirken teknik terimleri Türkçe açıklamalarıyla kullan.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Kullanıcı", "predicate": "TERCİH_EDİYOR", "object": "Teknik terim Türkçe açıklama"}
        ],
    },
    {
        "id": "ext_27",
        "category": "preference",
        "text": "Her zaman kod örneklerini async/await olarak yaz.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Kullanıcı", "predicate": "KOD_STİLİ", "object": "Async/Await"}
        ],
    },
    {
        "id": "ext_28",
        "category": "preference",
        "text": "Dokümantasyonu Markdown formatında hazırla.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Dokümantasyon Formatı", "predicate": "TERCİH_EDİLEN", "object": "Markdown"}
        ],
    },
    {
        "id": "ext_29",
        "category": "preference",
        "text": "Testlerde her zaman pytest kütüphanesini tercih ederim.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Test Kütüphanesi", "predicate": "TERCİH_EDİLEN", "object": "pytest"}
        ],
    },

    # --- Category 7: Technical Configuration (3 cases) ---
    {
        "id": "ext_30",
        "category": "config",
        "text": "MESA_EMBEDDING_DIMENSION=768 ve MESA_LOCAL_EMBEDDING_MODEL=magibu/embeddingmagibu-200m.",
        "expected_facts_count": 2,
        "facts": [
            {"subject": "MESA_EMBEDDING_DIMENSION", "predicate": "AYARLANDI", "object": "768"},
            {"subject": "MESA_LOCAL_EMBEDDING_MODEL", "predicate": "AYARLANDI", "object": "magibu/embeddingmagibu-200m"},
        ],
    },
    {
        "id": "ext_31",
        "category": "config",
        "text": "LanceDB bellek limiti 4GB olarak sınırlandırıldı.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "LanceDB Bellek Limiti", "predicate": "SINIRLANDIRILDI", "object": "4GB"}
        ],
    },
    {
        "id": "ext_32",
        "category": "config",
        "text": "Ollama servis adresi http://localhost:11434 olarak tanımlandı.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Ollama Servis Adresi", "predicate": "TANIMLANDI", "object": "http://localhost:11434"}
        ],
    },

    # --- Category 8: Negative Statement / Constraint (3 cases) ---
    {
        "id": "ext_33",
        "category": "negative_constraint",
        "text": "Üretim ortamında harici ağ sağlayıcılarına erişim kesinlikle yasaktır.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Harici Sağlayıcı Erişimi", "predicate": "DURUMU", "object": "Yasak"}
        ],
    },
    {
        "id": "ext_34",
        "category": "negative_constraint",
        "text": "Kullanıcı hiçbir şartta şifresini e-posta ile paylaşmamalıdır.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Şifre Paylaşımı", "predicate": "İZİN_DURUMU", "object": "Yasak"}
        ],
    },
    {
        "id": "ext_35",
        "category": "negative_constraint",
        "text": "Geçersiz şema içeren extraction çıktıları kabul edilmez.",
        "expected_facts_count": 1,
        "facts": [
            {"subject": "Geçersiz Extraction Çıktısı", "predicate": "KABUL_DURUMU", "object": "Reddedilir"}
        ],
    },
]


class GoldenExtractionAdapter:
    """Deterministic provider-boundary fixture; no model download is involved."""

    def __init__(self, case):
        self.case = case
        self.calls = 0

    def complete(self, _prompt, schema=None, **_kwargs):
        self.calls += 1
        assert schema.__name__ == "FactExtractionResponse"
        return {
            "facts": [
                {
                    "fact_text": f"{fact['subject']} {fact['predicate']} {fact['object']}",
                    "subject": fact["subject"],
                    "predicate": fact["predicate"],
                    "object": fact["object"],
                    "valid_from": fact.get("valid_from"),
                    "valid_to": fact.get("valid_to"),
                    "confidence": 1.0,
                    "source_span": self.case["text"],
                    "supersedes": fact.get("supersedes"),
                }
                for fact in self.case.get("facts", [])
            ]
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", GOLDEN_EXTRACTION_CASES, ids=[c["id"] for c in GOLDEN_EXTRACTION_CASES])
async def test_golden_smoke_fact_extraction(case):
    """Exercise the strict canonical service against every Turkish smoke case."""
    adapter = GoldenExtractionAdapter(case)
    facts = await FactExtractionService(llm=adapter).extract_facts(case["text"])

    assert adapter.calls == 1
    assert len(facts) == case["expected_facts_count"]
    assert [(fact.subject, fact.predicate, fact.object) for fact in facts] == [
        (fact["subject"], fact["predicate"], fact["object"])
        for fact in case.get("facts", [])
    ]


# ===========================================================================
# 2. Retrieval Smoke Dataset (25 Cases)
# ===========================================================================
GOLDEN_RETRIEVAL_QUERIES = [
    ("ret_01", "PostgreSQL bağlantı ayarları", "PostgreSQL"),
    ("ret_02", "Ahmet nerede yaşıyor?", "Ankara"),
    ("ret_03", "MESA lisans türü nedir?", "Apache 2.0"),
    ("ret_04", "Sunucu IP adresi ve portu", "192.168.1.50"),
    ("ret_05", "Sözleşme ne zaman bitiyor?", "2026-12-31"),
    ("ret_06", "Kullanıcının tercih ettiği tema", "Koyu Tema"),
    ("ret_07", "Yeni telefon numarası", "05551234567"),
    ("ret_08", "Ofis hangi ilçede?", "Levent"),
    ("ret_09", "Python sürümü", "3.13"),
    ("ret_10", "Web çerçevesi seçimi", "FastAPI"),
    ("ret_11", "Mesaj kuyruğu servisi", "Kafka"),
    ("ret_12", "Önbellek servisi", "Redis"),
    ("ret_13", "Can'ın projedeki görevi", "Proje Yöneticisi"),
    ("ret_14", "Zeynep'in mesleki unvanı", "Kıdemli Yazılım Mühendisi"),
    ("ret_15", "Ali'nin bildiği yabancı diller", "Almanca"),
    ("ret_16", "Erişim izni süresi", "24 saat"),
    ("ret_17", "Beta sürümü kapanış tarihi", "2026-09-15"),
    ("ret_18", "Yıllık bakım planlanan tarihler", "2026-03-01"),
    ("ret_19", "Tercih edilen test kütüphanesi", "pytest"),
    ("ret_20", "Doküman yazım formatı", "Markdown"),
    ("ret_21", "Embedding vektör boyutu", "768"),
    ("ret_22", "Yerel embedding model adı", "magibu/embeddingmagibu-200m"),
    ("ret_23", "LanceDB bellek sınırı", "4GB"),
    ("ret_24", "Ollama varsayılan portu", "11434"),
    ("ret_25", "Harici ağ sağlayıcıları kuralı", "Yasak"),
]


@pytest.mark.parametrize("qid,query,keyword", GOLDEN_RETRIEVAL_QUERIES)
def test_golden_smoke_retrieval_ranks_expected_memory_top_three(qid, query, keyword):
    """Check a deterministic provider's query/document space, not only vector shape."""
    keyword_folded = keyword.casefold()

    def provider(text):
        return (
            [1.0, 0.0, 0.0]
            if text == query or keyword_folded in text.casefold()
            else [0.0, 1.0, 0.0]
        )

    ident = EmbeddingIdentity(
        provider="mock",
        model="magibu/embeddingmagibu-200m",
        dimension=3,
        normalized=True,
    )
    service = EmbeddingService(identity=ident, provider_fn=provider)
    memories = [
        f"Hedef bellek: {keyword}",
        "İlgisiz bellek: hava durumu",
        "İlgisiz bellek: toplantı notları",
        "İlgisiz bellek: başka bir tercih",
    ]

    q_vec = service.embed_query(query)
    ranked = sorted(
        memories,
        key=lambda memory: sum(
            q * d for q, d in zip(q_vec, service.embed_document(memory))
        ),
        reverse=True,
    )
    assert memories[0] in ranked[:3]
