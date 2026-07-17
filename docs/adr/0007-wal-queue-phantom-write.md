# 7. WAL Queue ve Phantom Write Çözümü

Date: 2026-07-17

## Status

Accepted

## Context

LanceDB ve SQLite gibi farklı depolama katmanları kullanıldığında, vektör motoru ile ilişkisel veritabanı arasında senkronizasyon hataları oluşabilmektedir. Verinin bir katmana yazılıp diğerine yazılmadan işlemin kesilmesi "Phantom Write" durumuna yol açar.

## Decision

Sisteme kalıcı bir SQLite tabanlı Write-Ahead Log (WAL) kuyruğu (`lancedb_wal`) entegre edilmiştir. Tüm asenkron yazma işlemleri önce bu kuyruğa kaydedilir. Vektör (örneğin Procrustes transformasyonu) ve graf katmanlarındaki yazma işlemleri başarılı olduğunda kuyruktan temizlenerek nihai onay (commit) verilir.

## Consequences

- **Positive:** Eventual consistency (nihai tutarlılık) sağlanır ve "Phantom Write" önlenir. Veri kaybı riski azalır.
- **Negative:** Yazma işlemlerine ek bir katman girdiği için gecikme (latency) artar.
