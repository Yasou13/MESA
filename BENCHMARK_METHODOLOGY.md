# MESA Benchmark Metodolojisi

> [!NOTE]
> MESA benchmark suite'i bağımsız `mesa_benchmark` reposuna taşınmıştır.
> Kanonik karşılaştırmalı benchmark metodolojisi, dataset şemaları ve değerlendirme kriterleri için `mesa_benchmark` reposundaki `docs/METHODOLOGY.md` dokümanına başvurunuz.

## MESA Mimari Uyumluluk Notu

MESA çekirdeği bellek/retrieval adaptörleri (`KùzuDB + HybridRetriever`) için bağımsız `mesa_benchmark` suite'ini desteklemeye devam eder.
CI regresyonları için `tests/test_v4_rrf_ablation.py` ve `mesa_evals.v4_rrf_ablation` MESA core içerisinde korunmaktadır.
