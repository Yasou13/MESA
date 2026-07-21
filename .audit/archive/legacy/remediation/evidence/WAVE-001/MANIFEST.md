# Evidence Manifest — WAVE-001

| File | Bytes | SHA-256 |
|---|---:|---|
| `after.txt` | 116 | 4bd72c762586956a64c1dcbbf5c76ea18adaca839557369eeedcb6a6064aa193 |
| `before.txt` | 146 | 6167082296fbe51b776985bdeec99adc15483dd332f4dc5d87ecfa2dae4a7189 |
| `changed-files.txt` | 87 | 5fa4f62d2867cc797981f759bef9eb66769f3aebf5ca60890e8936d8b6459818 |
| `commands.log` | 120 | 098b20ff638c8f437ba8f30fa72b940245931100b8b8fe455a27a1cd2269dffe |
| `compile-check.txt` | 504 | af9d052eb3e6c116e5d594ffdd0059c4a96a4c05163ee989c3a736b436e1a043 |
| `cross-system-check.md` | 174 | 6e6b5e0b37858d4d5c3f15f72bae2f8ba55b0f12c2fd3c8b0793c34f03e14597 |
| `diff-stat.txt` | 40 | 09172c4b8169b9b7470de103c932e6c4a6659e8eb30a50118d6535381f2c2768 |
| `patch-transport-error.txt` | 431 | 214d85877969091f92aeec27bd00eb90e2fa8219c6c6a21b9fcabf9027baece0 |
| `recovery-actions.md` | 469 | dd745abb645d523b160f6a6cf3a87651a50d8d31438e8c35e1a19a3f121bb76e |
| `regression-tests.txt` | 1167 | 025a40bdb76af2999cc541a70df67e155f21b0328f96a1709c14cd7e2fcf75f5 |
| `resource-summary.txt` | 127 | bca6ce06c9c22ab1c10e6af9565625b9b50d06c7c4b68ae5f73a6bbbc4bcbcb6 |
| `rollback-status.txt` | 588 | 696f8f78f4d4cc05d9a9fd23ba14cc9c2b317d467d6c408c9779116cc2369c73 |
| `source-after-hashes.txt` | 940 | c70b108d941880155023b7b829b9ef508579299897815ed7476f3b11454204d0 |
| `source-before-hashes.txt` | 1817 | c623965b03837d3a4e41c92f5230aedff610029a5263cfb46dcc181703b5729c |
| `source-diff.txt` | 9358 | 66081a5ad2bb4e954b3d8791048159d9f52b83d38faf1330d0359679496a47a5 |
| `source-edit-method.md` | 1071 | 31ae72f5cb673d78c01d872fa1ac74873b68100c9e702dcbf2793d27bf48452d |
| `target-test.txt` | 654 | fb3d997a43e186e7e5c1be7dbf52597ecbfb1caa23a252daf8ede56cf545845e |
| `tests.txt` | 1063 | 5e39f6b7c40d4aaccdb3663b4847c632adfca958e1fa9d4a55d86bceaef53860 |

## External rollback material

| Path | Files | Integrity |
|---|---|---|
| `/storage/mesa-lab/artifacts/WAVE-001/source-backup/` | `server.py`, `rbac.py`, `router.py`, `run_server.py`, `test_principal_authorization.py` | Each pre-edit SHA-256 and size is recorded in `source-before-hashes.txt`; backups are outside Git. |

Evidence level: E2 for reproduction, source fix and focused component tests. E3/E4 are not claimed. No secret, provider, Ollama, production system or production data was used.
