from pathlib import Path
from typing import Any, Dict, Optional


class MarkdownReporter:
    def __init__(self, run_id: str, config: Any, output_dir: Optional[str] = None):
        self.run_id = run_id
        self.config = config
        self.output_dir = Path(output_dir) if output_dir else Path("results")

    def generate_report(self, metrics: Any) -> str:
        """Generate report from a BenchmarkMetrics object."""
        metrics_dict = (
            metrics.model_dump()
            if hasattr(metrics, "model_dump")
            else (
                metrics.dict()
                if hasattr(metrics, "dict")
                else vars(metrics) if hasattr(metrics, "__dict__") else metrics
            )
        )
        return self.generate_report_from_dict(metrics_dict)

    def generate_report_from_dict(
        self, metrics_dict: Dict[str, Any], output_path: Optional[str] = None
    ) -> str:
        """
        Generates a markdown report directly from a metrics dictionary.
        """
        # (Assuming the dict matches BenchmarkMetrics structure)
        m = metrics_dict

        # Core Metrics
        total_q = m.get("total_questions", 0)
        correct_q = m.get("correct_answers", 0)
        accuracy = m.get("accuracy", 0.0) * 100

        # Retrieval Metrics
        hit1 = m.get("hit_at_1", 0.0) * 100
        hit3 = m.get("hit_at_3", 0.0) * 100
        hit5 = m.get("hit_at_5", 0.0) * 100
        mrr = m.get("mrr", 0.0) * 100
        ndcg = m.get("ndcg", 0.0) * 100

        # Latency Metrics
        avg_lat = m.get("avg_latency_ms", 0.0)
        p95_lat = m.get("p95_latency_ms", 0.0)
        p99_lat = m.get("p99_latency_ms", 0.0)

        report_lines = [
            "# 📊 MESA Benchmark Report",
            f"**Suite:** `{self.config.suite_name}` | **Run ID:** `{self.run_id}` | **Graph Architecture:** `KùzuDB + HybridRetriever (Multi-Hop Enabled)`",
            "",
            "---",
            "",
            "## 🎯 1. Accuracy & Reliability (Doğruluk ve Güvenilirlik)",
            "Bu bölüm, sistemin üretilen sorulara ne kadar doğru cevap verebildiğini gösterir.",
            "",
            "| Metric | Value | Açıklama |",
            "|:---|:---|:---|",
            f"| **Total Questions** | {total_q} | Test edilen toplam soru sayısı |",
            f"| **Correct Answers** | {correct_q} | Tamamen doğru kabul edilen cevaplar |",
            f"| **Accuracy** | %{accuracy:.2f} | Sistemin genel doğruluk oranı |",
            "",
        ]

        metrics_list = self.config.evaluation.metrics if self.config else []

        # Agreement Section
        agreement_data = metrics_dict.get("agreement", {})
        if (
            self.config and getattr(self.config.evaluation, "enable_agreement", False)
        ) or agreement_data:
            if agreement_data:
                report_lines.extend(
                    [
                        "### 🤝 Methodological Verification (Keyword vs LLM-Judge Agreement)",
                        "",
                        "Bu tablo, keyword/exact-match evaluator ile LLM-Judge evaluator arasındaki uyumu gösterir.",
                        "Yüksek uyum oranı (≥80%) keyword matching'in güvenilir bir proxy olduğunu kanıtlar.",
                        "",
                        "| Evaluator Pair | Agreement Rate (%) | Cohen's Kappa | Assessment |",
                        "|:---|:---|:---|:---|",
                    ]
                )

                kappa = agreement_data.get("cohens_kappa", 0)
                rate = agreement_data.get("agreement_rate", 0)
                assessment = (
                    "✅ Yüksek Uyum — Güvenilir Proxy"
                    if kappa >= 0.7
                    else "⚠️ Düşük Uyum — LLM-Judge zorunlu"
                )
                report_lines.extend(
                    [
                        f"| **Keyword vs LLM-Judge** | **%{rate:.2f}** | `{kappa:.4f}` | {assessment} |"
                    ]
                )

                conf = agreement_data.get("contingency_table", {})
                if conf:
                    tt = conf.get("both_correct", 0)
                    tf = conf.get("only_a_correct", 0)
                    ft = conf.get("only_b_correct", 0)
                    ff = conf.get("both_incorrect", 0)

                    report_lines.extend(
                        [
                            "",
                            "**Contingency Table:**",
                            "",
                            "| | Judge: Correct | Judge: Incorrect |",
                            "|:---|:---|:---|",
                            f"| **Keyword: Correct** | {tt} | {tf} |",
                            f"| **Keyword: Incorrect** | {ft} | {ff} |",
                        ]
                    )

        if "latency" in metrics_list:
            report_lines.extend(
                [
                    "",
                    "## ⚡ 2. Speed & Latency (Hız ve Gecikme)",
                    "Sistemin veritabanından bağlamı ne kadar sürede getirdiğini ölçer. Daha düşük her zaman daha iyidir.",
                    "",
                    "| Metric | Time (ms) | Açıklama |",
                    "|:---|:---|:---|",
                    f"| **Average Latency** | {avg_lat:.2f} ms | Ortalama tepki süresi |",
                    f"| **P95 Latency** | {p95_lat:.2f} ms | Sorguların %95'i bu süreden daha hızlı bitti |",
                    f"| **P99 Latency** | {p99_lat:.2f} ms | En zorlu sorguların maksimum süresi |",
                ]
            )

        if "hit_at_k" in metrics_list or "mrr" in metrics_list:
            report_lines.extend(
                [
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
                    f"| **nDCG@5** | %{ndcg:.2f} | Normalize Edilmiş İndirgenmiş Kümülatif Kazanç (Top-5) |",
                    "",
                    "> 💡 **İpucu:** `Hit@K` oranları ve Multi-Hop Çizge entegrasyonu sayesinde MESA hafıza katmanı, ilişkili varlıklar arasındaki uzun zincirli çıkarımlarda standart Vektör+SQLite sistemlerinden net şekilde ayrışmaktadır.",
                ]
            )

        if "efficiency" in metrics_list:
            token_eff = m.get("token_efficiency")
            if token_eff is not None:
                report_lines.extend(
                    [
                        "",
                        "## 💎 4. Token Efficiency",
                        "Doğru bir cevap üretmek için sistemin ve LLM'in maliyet/token performansını gösterir.",
                        "",
                        "| Metric | Value | Açıklama |",
                        "|:---|:---|:---|",
                        f"| **Tokens / Correct Answer** | {token_eff:.1f} | 1 doğru cevap başına harcanan ortalama token (Prompt + Completion) |",
                    ]
                )

        # Root-Cause & Bottleneck Diagnostics Section
        failure_attrs = m.get("failure_attributions", {})
        latency_breakdown = m.get("avg_latency_breakdown_ms", {})
        if failure_attrs or latency_breakdown:
            report_lines.extend(
                [
                    "",
                    "## 🛠️ 5. Root-Cause & Bottleneck Diagnostics (Zafiyet ve Darboğaz Analizi)",
                    "Bu bölüm, sistemin sadece ne kadar puan aldığını değil, başarısız veya yavaş olan sorguların **doğrudan kaynağını (bottleneck)** gösterir.",
                    "",
                ]
            )

            if failure_attrs:
                total_fails = sum(failure_attrs.values())
                report_lines.extend(
                    [
                        "### 🔬 Failure Source Attribution (Hata Kaynağı Dağılımı)",
                        f"Toplam başarısız/düşük puanlı sorgu sayısı: **{total_fails}**",
                        "",
                        "| Hata Kaynağı (Root Cause) | Sayı | Oran (%) | Teşhis & Anlamı |",
                        "|:---|:---|:---|:---|",
                    ]
                )
                descriptions = {
                    "RETRIEVAL_MISS": "🔴 **Arama Zafiyeti:** Beklenen bağlam Vektör/Çizge tarafından bulunamadı.",
                    "CONTEXT_NOISE": "🟡 **Graf Gürültüsü:** Doğru bağlam geldi ama Multi-Hop aşırı uç ekleyip LLM'i şaşırttı.",
                    "LLM_REASONING_ERROR": "🔵 **LLM Mantık Zafiyeti:** Doğru bağlam 1. sırada geldi ama LLM cevabı çıkaramadı.",
                    "TIMEOUT_OR_ERROR": "⚫ **Zaman Aşımı / Sistem Hatası:** Sorgu süresi doldu veya exception fırlatıldı.",
                }
                for attr, count in failure_attrs.items():
                    pct = (count / total_fails * 100) if total_fails > 0 else 0.0
                    desc = descriptions.get(attr, "Bilindışı hata kaynağı.")
                    report_lines.append(
                        f"| **{attr}** | {count} | %{pct:.1f} | {desc} |"
                    )
                report_lines.append("")

            if latency_breakdown:
                total_bd = sum(latency_breakdown.values())
                report_lines.extend(
                    [
                        "### ⏱️ Internal Latency Breakdown (Katman Darboğazları)",
                        "Her bir arama katmanının ortalama ne kadar milisaniye harcadığı ve toplam gecikmedeki payı:",
                        "",
                        "| Arama Katmanı | Ortalama Süre (ms) | Pay (%) | Durum |",
                        "|:---|:---|:---|:---|",
                    ]
                )
                for stage, ms in sorted(
                    latency_breakdown.items(), key=lambda x: x[1], reverse=True
                ):
                    if stage == "total_retrieval_ms":
                        continue
                    pct = (ms / total_bd * 100) if total_bd > 0 else 0.0
                    status = (
                        "⚠️ **DARBOĞAZ**"
                        if pct > 50
                        else ("🟢 Normal" if pct < 20 else "🟡 İzlenmeli")
                    )
                    report_lines.append(
                        f"| **{stage}** | {ms:.2f} ms | %{pct:.1f} | {status} |"
                    )
                report_lines.append("")

        report_lines.append("")

        if output_path is None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(self.output_dir / f"report_{self.run_id}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        return output_path
