# Evidence Manifest — WAVE-002

| File | Bytes | SHA-256 |
|---|---:|---|
| `after.txt` | 431 | 167a35238fa3f994009649aa9bf70da5141765323bf53631db08f281f46ea9a6 |
| `before.txt` | 519 | 4d99242090f7b31eece2742b3d15be3fe26e012fd12ad5adeada98602dee2377 |
| `changed-files.txt` | 95 | 0eed384cecba8dc72223512b745b3011d062c926f3f624f1938c9d80789ae776 |
| `commands.log` | 855 | f4b4bcc1d696b543cbf1108aa99d387f6ace3e3a12d67f42c302583b6e3a7cfa |
| `compile-check.txt` | 177 | c59bc5cc1c5a5518c9ee87ca14708e9013e0ba04cf1393d785cc4027c773d0de |
| `cross-system-check.md` | 484 | 0d27eb25e4270f3f621abef80d593460de320514d85881ee5d399c26d5ea93bb |
| `data001-after.txt` | 735 | 29de1a2addb53f9383ff24a5f0c57fbfb0520fc639a6c190648739e2f1141780 |
| `data001-before.txt` | 378 | 7e2f5d9386824a7782ea5565758e438bd9bdde9b7a14f5f84421161913d89785 |
| `data001-migration.md` | 369 | 7d016306b7c762209a1986edb6f278efe46a7ad3357a15bb4b86c85e935a0b10 |
| `data001-source-after-hashes.txt` | 590 | 6777caa2f36053c1fe76b3ae8867fef0bdad16c3eca088ae2aadb9477c39d4d7 |
| `data001-source-diff.txt` | 28626 | 56b4ec025d378b6e0ba5baba209c1d7d4544e8173c710430373351071dd7b320 |
| `data001-tests.txt` | 1019 | bf8ec685db635f1ed5e777d8f3dd039162b5951cc2d0474f899e746bbeff3c8c |
| `diff-stat.txt` | 59 | 069d269eaec73961f40a2620cc39cdcec93a3197fbf4e6c982c8290290e1dbfe |
| `regression-tests.txt` | 566 | ca1e9d696934ed4a5224afd5b2a7a049e16c23d294ef27627cce30f605d49888 |
| `resource-summary.txt` | 176 | 045e58cdb890ef42fc6f72fab0c098d3485458189bcb12711b093b8f3106208d |
| `rollback-status.txt` | 268 | dae0a6d277e4ef6dd8ac584c91e7aee89bda3fdb1ae5fb46accaa8dfe4eb8e5f |
| `source-after-hashes.txt` | 293 | 14f24028b1fb9e2bb6728f5a4c04507259c38099f289ace00a8156df76e63f83 |
| `source-before-hashes.txt` | 152 | 7cb5803aaa24d2a38a94ff9edccdf482b36672a20a26a013329bdf776ff8e2be |
| `source-diff.txt` | 4045 | c51067f8768d6187065140ad86fd7a06ba27f41d080cb86af441e0bef04d7f69 |
| `source-edit-method.md` | 397 | ce90cd11c7c21c7a54eb8e86f482ba0b1f8510be611424996756550064e2e606 |
| `target-test.txt` | 618 | 09be7839182d5d8e6e940ebf48772e015fdb2f934f8feaf44a9838177f3305b2 |

## External rollback material

| Path | Files | Integrity |
|---|---|---|
| `/storage/mesa-lab/storage/WAVE-002/source-backup/` | WAVE-002 DATA-002/004 pre-edit copies | Recorded before/after artifacts |
| `/storage/mesa-lab/storage/WAVE-002/source-backup-data001/` | DATA-001 pre-edit copies | Current owned hashes in `data001-source-after-hashes.txt` |

Evidence level: E2 deterministic component and synthetic migration evidence. E3/E4 are not claimed. No secret, provider, Ollama, Docker, production system, or production data was used.
