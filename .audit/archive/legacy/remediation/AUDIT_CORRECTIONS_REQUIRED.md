# Audit Corrections Required

Bu dosya audit-only düzeltme gereksinimidir; source remediation veya finding ID üretmez.

| Öncelik | Hedef kayıt | Gerekli düzeltme | Kanıt |
|---|---|---|---|
| P0 release gate | `RELEASE_GATE_MATRIX.md`, `FINAL_FINDING_MATRIX.md` / `RELEASE-001` | Package artifact `PASS` demeyin. `RELEASE-001`i doğrulanmış açık artifact hygiene/reproducibility kusuru yapın; wheel package discovery'de `__pycache__`/`.pyc` exclusion ve temiz rebuild sonrası checksum karşılaştırması gereklidir. | Final wheel ve audit rebuild'i `mesa_storage/alembic/**/__pycache__/*.pyc` içeriyor; SHA-256 `138617…c9801` ≠ `4337af…c675`. |
| P0 release gate | `MASTER_CLOSURE_REPORT.md`, `RELEASE_GATE_MATRIX.md`, `FINAL_FINDING_MATRIX.md` / `ENV-001`, `OPS-001` | “`pip check` üç optional conflict” ifadesini kaldırın. `rich==13.9.4`, project/wheel core requirement `rich>=15.0.0` ile uyumsuzdur. Test source tree'nin direct importu nedeniyle pip check core package metadata'sını temsil etmez. | `pyproject.toml` dependencies ve wheel METADATA; `pip show rich` = 13.9.4; `pip check` üç conflict. |
| P1 kayıt doğruluğu | `FINAL_TEST_MATRIX.md`, `RELEASE_GATE_MATRIX.md` / `TEST-001` | “Clean full suite post-repair rerun NOT_REPEATED” yerine bounded local clean suite `900 passed` bilgisini ekleyin. `TEST-001`i CI/coverage/external kapsam açık olduğu için FBNV bırakabilirsiniz. | Audit command 322.67 s, 900 passed. |
| P2 test komutu | `COMMAND_LOG.md` ve gelecekteki CI/local test talimatı | Safe suite seçimi `--deselect` değil `--ignore=tests/test_mem0.py` kullanmalıdır; collect-only deselect yolunda Qdrant teardown warning gözlendi. | Collect-only gözlemi; `--ignore` ile full safe suite 900/900. |
| P2 kayıt netliği | `STATE.md`, `QUEUE.md`, `WAVES.md`, `REGRESSION_LOG.md` | Tarihsel tablolara açık “superseded by master closure final state” işareti veya üstte canonical latest-state referansı ekleyin. Append-only geçmiş korunabilir, fakat W3/W4 ve campaign durumları ilk okunuşta eski/pending görünüyor. | Master closure final append'i eski pending tabloların altında yer alıyor. |

Bu düzeltmeler uygulanmadan independent audit sonucu `AUDIT_PASS` değildir. Release kararı
zaten `NO_GO` kaldığından burada yeni bir production kararı verilmemiştir.

## New independent audit findings

| ID | Severity / category | Claim | Actual / evidence | Impact and correction |
|---|---|---|---|---|
| AUDIT-001 | HIGH / FALSE_CLOSURE | `DATA-005` `VERIFIED_RESOLVED`, real-store E3 failure/restart claimed | Bounded SQLite/WAL contracts pass; preserved W3 evidence is `UNKNOWN_OR_UNVERIFIABLE`, and no independent real-store failure/restart proof supports the stronger claim. | Reclassify `DATA-005` to `FIXED_NOT_VERIFIED`; P0 release blocker remains. |
| AUDIT-002 | HIGH / FALSE_CLOSURE | `DLQ-001` `VERIFIED_RESOLVED`, production consumer bridge claimed | JSONL receipt/restart/poison contracts pass; Docker/deployed consumer topology was not run. | Reclassify `DLQ-001` to `FIXED_NOT_VERIFIED`; P0 release blocker remains. |
| AUDIT-003 | HIGH / ARTIFACT_DEFECT | Final wheel gate `PASS` | Final/rebuilt wheels contain Python-version-specific `__pycache__/*.pyc`; rebuild hash differs. | Keep `RELEASE-001` OPEN; exclude cache files and prove clean reproducible artifact. |
| AUDIT-004 | HIGH / EVIDENCE_GAP | All `pip check` conflicts optional | Wheel metadata declares core `rich>=15.0.0`; installed rich is 13.9.4. | Correct ENV/OPS dependency classification; clean package install gate required. |
| AUDIT-005 | MEDIUM / OPTIONAL_DEPENDENCY_ISOLATION | `--deselect test_mem0` safely excludes Mem0 | Deselection still permitted import-side-effect/Qdrant teardown warning during earlier collection. | Canonical bounded command must use `--ignore=tests/test_mem0.py`; no source/test skip change. |
| AUDIT-006 | MEDIUM / REPORT_INCONSISTENCY | Historical state tables read as current | `STATE.md`/`QUEUE.md`/`WAVES.md` retain pending states below later closure append. | Add explicit supersession/current-state pointer; preserve history. |
