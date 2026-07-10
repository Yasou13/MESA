import json
from pathlib import Path
from typing import Dict, Any

class MarkdownReporter:
    def __init__(self, run_id: str, config: Any):
        self.run_id = run_id
        self.config = config

    def generate_report(self, metrics: Any) -> str:
        """Generate report from a BenchmarkMetrics object."""
        metrics_dict = metrics.model_dump() if hasattr(metrics, 'model_dump') else metrics.dict() if hasattr(metrics, 'dict') else vars(metrics) if hasattr(metrics, '__dict__') else metrics
        return self.generate_report_from_dict(metrics_dict)

    def generate_report_from_dict(self, metrics_dict: Dict[str, Any]) -> str:
        """Generate report from a plain dictionary (supports injected agreement/variance data)."""
        # Safely extract metrics
        total = metrics_dict.get('total_questions', 0)
        correct = metrics_dict.get('correct_answers', 0)
        acc = metrics_dict.get('accuracy', 0.0) * 100
        avg_lat = metrics_dict.get('avg_latency_ms', 0.0)
        p95_lat = metrics_dict.get('p95_latency_ms', 0.0)
        p99_lat = metrics_dict.get('p99_latency_ms', 0.0)
        
        hit1 = metrics_dict.get('hit_at_1', 0.0) * 100
        hit3 = metrics_dict.get('hit_at_3', 0.0) * 100
        hit5 = metrics_dict.get('hit_at_5', 0.0) * 100
        mrr = metrics_dict.get('mrr', 0.0) * 100

        agreement = metrics_dict.get('agreement', {})
        variance = metrics_dict.get('variance', {})

        report_lines = [
            f"# 📊 MESA Benchmark Report",
            f"**Suite:** `{self.config.suite_name}` | **Run ID:** `{self.run_id}` | **Graph Architecture:** `KùzuDB + HybridRetriever (Multi-Hop Enabled)`",
            "",
            "---",
            "",
            "## 🎯 1. Accuracy & Reliability (Doğruluk ve Güvenilirlik)",
            "Bu bölüm, sistemin üretilen sorulara ne kadar doğru cevap verebildiğini gösterir.",
            "",
            "| Metric | Value | Açıklama |",
            "|:---|:---|:---|",
            f"| **Total Questions** | {total} | Test edilen toplam soru sayısı |",
            f"| **Correct Answers** | {correct} | Tamamen doğru kabul edilen cevaplar |",
            f"| **Accuracy** | %{acc:.2f} | Sistemin genel doğruluk oranı |",
            ""
        ]

        if agreement and agreement.get("total", 0) > 0:
            agr_rate = agreement.get("agreement_rate", 0.0)
            kappa = agreement.get("cohens_kappa", 0.0)
            contingency = agreement.get("contingency_table", {})
            report_lines.extend([
                "### 🤝 Methodological Verification (Keyword vs LLM-Judge Agreement)",
                "",
                "Bu tablo, keyword/exact-match evaluator ile LLM-Judge evaluator arasındaki uyumu gösterir.",
                "Yüksek uyum oranı (≥80%) keyword matching'in güvenilir bir proxy olduğunu kanıtlar.",
                "",
                "| Evaluator Pair | Agreement Rate (%) | Cohen's Kappa | Assessment |",
                "|:---|:---|:---|:---|",
                f"| **Keyword Match vs LLM-Judge** | **%{agr_rate:.2f}** | `{kappa:.4f}` | {'✅ Yüksek Uyum — Güvenilir Proxy' if agr_rate >= 80.0 else '⚠️ Orta/Düşük Uyum — LLM-Judge Raporlaması Önerilir'} |",
                "",
            ])
            if contingency:
                report_lines.extend([
                    "**Contingency Table:**",
                    "",
                    "| | Judge: Correct | Judge: Incorrect |",
                    "|:---|:---|:---|",
                    f"| **Keyword: Correct** | {contingency.get('both_correct', 0)} | {contingency.get('only_a_correct', 0)} |",
                    f"| **Keyword: Incorrect** | {contingency.get('only_b_correct', 0)} | {contingency.get('both_incorrect', 0)} |",
                    "",
                ])

        if variance and variance.get("n", 0) > 1:
            mean_str = variance.get("accuracy_mean_std", "N/A")
            p_val = variance.get("p_value_vs_baseline", "N/A")
            report_lines.extend([
                "### 📈 Multi-Seed Statistical Variance (Stokastik Kararlılık)",
                "",
                "LLM'ler stokastik olduğundan, aynı benchmark farklı seed'lerle çalıştırılır.",
                "Mean ± Std ve p-value, farkın istatistiksel olarak anlamlı olup olmadığını gösterir.",
                "",
                "| Metric | Multi-Seed Mean ± Std | p-value vs Baseline | Significance |",
                "|:---|:---|:---|:---|",
                f"| **Accuracy Across Seeds (n={variance.get('n')})** | **{mean_str}** | `{p_val}` | {'✅ Anlamlı Fark (p < 0.05)' if variance.get('is_significant') else '⚠️ İstatistiksel Olarak Eşdeğer'} |",
                ""
            ])

        report_lines.extend([
            "## ⚡ 2. Speed & Latency (Hız ve Gecikme)",
            "Sistemin veritabanından bağlamı ne kadar sürede getirdiğini ölçer. Daha düşük her zaman daha iyidir.",
            "",
            "| Metric | Time (ms) | Açıklama |",
            "|:---|:---|:---|",
            f"| **Average Latency** | {avg_lat:.2f} ms | Ortalama tepki süresi |",
            f"| **P95 Latency** | {p95_lat:.2f} ms | Sorguların %95'i bu süreden daha hızlı bitti |",
            f"| **P99 Latency** | {p99_lat:.2f} ms | En zorlu sorguların maksimum süresi |",
            "",
            "## 🔍 3. Retrieval Performance (Hafıza Bulma Başarısı)",
            "Sistemin Vektör + KùzuDB Çizge arama mimarisinin doğru bilgiyi ne kadar isabetli bulduğunu gösterir.",
            "",
            "| Metric | Score | Açıklama |",
            "|:---|:---|:---|",
            f"| **Hit@1** | %{hit1:.2f} | Doğru bilgi 1. sırada geldi |",
            f"| **Hit@3** | %{hit3:.2f} | Doğru bilgi ilk 3 sonuç içinde yer aldı |",
            f"| **Hit@5** | %{hit5:.2f} | Doğru bilgi ilk 5 sonuç içinde yer aldı |",
            f"| **MRR** | %{mrr:.2f} | Ortalama İlk Bulma Sırası (Mean Reciprocal Rank) |",
            "",
            "> 💡 **İpucu:** `Hit@K` oranları ve Multi-Hop Çizge entegrasyonu sayesinde MESA hafıza katmanı, ilişkili varlıklar arasındaki uzun zincirli çıkarımlarda standart Vektör+SQLite sistemlerinden net şekilde ayrışmaktadır.",
            ""
        ])
                
        report_path = f"report_{self.run_id}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        return report_path
