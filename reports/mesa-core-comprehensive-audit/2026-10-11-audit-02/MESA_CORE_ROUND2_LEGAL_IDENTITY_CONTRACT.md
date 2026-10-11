# MESA Core Round 2 — Yasal Kimlik Sözleşmesi

## Typed giriş

`V4LegalIdentityRequest` alanları:

| Alan | Zorunlu | Semantik |
|---|---:|---|
| `law_id` | evet | tanınan kanun numarası, kısa kodu veya canonical alias |
| `statute_code` | hayır | varsa `law_id` ile aynı ontology girdisine çözülmeli |
| `article_id` | evet | `madde-1`, `1`, `1A` biçimleri canonical maddeye çözülür |
| `version_id` | hayır | producer'ın doğruladığı sürüm/effective snapshot kimliği |
| `paragraph_id` | hayır | nested fıkra kimliği |
| `clause_id` | hayır | bent/alt hüküm kimliği |

Canonical örnek:

```json
{
  "law_id": "6098",
  "statute_code": "TBK",
  "article_id": "1",
  "version_id": "2026-current",
  "paragraph_id": "1",
  "clause_id": "a",
  "identity_source": "verified_ingestion"
}
```

Tanımsız alias, kanun/kod çelişkisi, eksik law/article, geçersiz karakter veya
caller-controlled source etiketi fail-closed `ValueError/409` üretir. `TBK`, `6098`
ve `Türk Borçlar Kanunu` aynı ontology girdisine çözülebilir; bare `MADDE 1` tek
başına kanun üretmez.

## Güven sınırı

- API session ve dataset WRITER yetkisi server-side doğrulanır.
- Caller `metadata` içindeki bütün `_mesa_*` anahtarları admission'da atılır.
- Yalnız ayrı typed alan canonicalize edilerek `_mesa_legal_identity` internal
  snapshot'ına dönüşür.
- Kaynak metnin bildirilen statute/article'a gerçekten ait olduğunu doğrulamak
  producer/catalog görevidir. MESA_Data bunu OCR/body tahmininden değil, güvenilir
  kaynak manifestinden sağlamalıdır.

## Kalıcılık eşlemesi

`memory_mutations.metadata_json._mesa_legal_identity` → projection mutation
`legal_identity` → `v4_assertions` kolonları:

- `version_id` → `legal_version_id`
- `identity_source` → `legal_identity_source`
- diğer typed alanlar aynı adla korunur.

Fallback triplet, trusted identity varsa subject'i `TBK m.1`, predicate'i `hükmü`
ve literal'i exact content yapar. Rich extraction triplet'leri korunur; legal kolonlar
triplet metninden bağımsız canonical provenance olarak eklenir.

## Retrieval ve temporal sözleşme

- Indexed selector: tenant + dataset + statute + article + version + status.
- Agent/COMMITTED, jurisdiction ve `valid_at|valid_from|valid_to` SQL'de rank öncesi.
- Current arama yalnız ACTIVE; historical arama ACTIVE/SUPERSEDED ve effective
  window kesişimini kullanır.
- Exact law/article `legal_factor=1.5`; aynı statute/farklı article `1.25`;
  competing typed statute `0.25`. Bunlar RRF'ye doğrudan puan değildir.
- `retrieval_provenance.legal_identity` canonical kimliği ve sürümü döndürür.

## Migrasyon / geriye uyumluluk

`d5e6f7a8b9c0` additive, non-null/empty-default kolonlar ve compound index ekler.
Eski satırlar empty identity ile text-only davranışı sürdürür; kimlik uydurulmaz.
Fresh ve `c4d5... → head` upgrade testleri FTS trigger'larının korunduğunu doğrular.
