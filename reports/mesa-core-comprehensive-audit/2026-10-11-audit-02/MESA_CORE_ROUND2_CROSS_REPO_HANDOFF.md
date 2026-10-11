# MESA Core Round 2 — Cross-Repo Handoff

## MESA_Data yükümlülükleri

MESA_Data bu çalışmada mevcut değildi ve değiştirilmedi. Producer her chunk için:

1. exact `content`, `source_ref`, document/revision/chunk kimlikleri;
2. doğrulanmış `law_id` ve `article_id`;
3. varsa canonical `statute_code`, `version_id`, `paragraph_id`, `clause_id`;
4. `valid_from`, `valid_to`, jurisdiction ve authority bilgisini;
5. bu kimliğin hangi source manifest/catalog kaydından doğrulandığını

sağlamalıdır. `MADDE 1` body metninden parent law tahmin edilmemeli; bilinmeyen veya
çelişkili kimlik typed alana yazılmamalıdır. Serbest `_mesa_*` metadata kabul edilmez.

Producer contract doğrulaması için MESA_Data ayrı testte en az şu vakaları göndermeli:
aynı madde numaralı iki kanun, iki effective revision, repealed/not-yet-effective,
nested fıkra/bent, uzun span ve missing identity.

## MESA_E2E_Certification yükümlülükleri

MESA_E2E_Certification değiştirilmedi. Historical Profile B, E2E-owned sealed context
adapter kullanır; ürün `ContextBuilder` sonuçlarıyla eş tutulmamalıdır. Operatör:

- bu dalın final doğrulanmış SHA'sını detached/frozen olarak fetch etmeli;
- corpus ve qrel freeze/checksum'larını değiştirmemeli;
- resmi 80-query TEST'i yalnız bağımsız sertifikasyon amacıyla çalıştırmalı;
- `/v4/memory/search limit=5` sonuçları ile E2E context packing arasındaki kaybı ayrı
  trace etmeli;
- lane recall, RRF, legal factor ve final evidence groups'u query bazında arşivlemelidir.

Bu çalışma resmi Profile B'yi çalıştırmadı ve historical MISS verdict'ini değiştirmedi.

## Model/provider yükümlülükleri

- Magibu model ID yanında resolved immutable digest/revision kaydedilmeli.
- Dimension 768 ve normalization/metric gerçek provider cevabıyla doğrulanmalı.
- Document/query formatting ve truncation provider dokümanıyla kanıtlanmalı.
- Rebuild gerçek fresh embedding request üretmeli; registry metadata relabel kabul
  edilmemeli.

## VM/Profile A operatörü

- Uzun native LanceDB soak: RSS/USS, fragments/versions, FD, optimize/cleanup,
  throughput ve p50/p99.
- Kùzu graph ON/OFF iki bağımsız evidence-group fixture, K=5.
- Restart/rebuild sonrası vector digest ve canonical provenance parity.
- CI sonucunu ve remote SHA'yı GitHub'da doğrulama.

## Sahiplik özeti

| Konu | Sahip | Durum |
|---|---|---|
| typed identity consumer/storage/retrieval | MESA Core | FIXED |
| source identity truth/manifest | MESA_Data | EXTERNAL_DEPENDENCY |
| sealed context ve resmi Profile B | MESA_E2E_Certification | NOT_RUN |
| Magibu digest/live quality | Provider/VM | NOT_RUN |
| uzun native memory soak | VM/Profile A | SOAK_NOT_RUN |
