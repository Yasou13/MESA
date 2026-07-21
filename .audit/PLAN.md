# Resolution Plan

## Active
- DATA-001: Journal tabanlı üç-store purge doğrulandı; gerçek SQLite/Kùzu E2 ve restore/deployed E3 doğrulaması bekliyor.

## Next
- External gate: Disposable SQLite/Kùzu purge failure/retry/recovery, gerçek Kùzu hata enjeksiyonu/reconciliation, DLQ executor concurrency ve Compose API+worker gate. Sonraki yerel remediation için MIG-004 değerlendirilir.

## Blocked
- OPS-001: `pip-tools` kurulu değil, pip cache erişilemez; bulk dependency çözümü/indirme ve temiz install yerel karar sınırı dışındadır. Hash'siz veya elde yazılmış lock kabul edilmeyecek.

## Deferred
- Yok

## Completed
- Yok
