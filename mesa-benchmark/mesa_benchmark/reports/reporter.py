import json
from pathlib import Path
from typing import Dict, Any

class MarkdownReporter:
    def __init__(self, run_id: str, config: Any):
        self.run_id = run_id
        self.config = config

    def generate_report(self, metrics: Any) -> str:
        metrics_dict = metrics.model_dump() if hasattr(metrics, 'model_dump') else metrics.dict() if hasattr(metrics, 'dict') else vars(metrics) if hasattr(metrics, '__dict__') else metrics
        
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

        report_lines = [
            f"# 📊 MESA Benchmark Report",
            f"**Suite:** `{self.config.suite_name}` | **Run ID:** `{self.run_id}`",
            "",
            "---",
            "",
            "## 🎯 1. Accuracy & Reliability (Doğruluk ve Güvenilirlik)",
            "Bu bölüm, sistemin üretilen sorulara ne kadar doğru cevap verebildiğini gösterir. (Not: Eğer LLM Yargıcı başarısız olup salt metin eşleştirmesine geçerse bu oran yapay olarak düşük çıkabilir).",
            "",
            "| Metric | Value | Açıklama |",
            "|:---|:---|:---|",
            f"| **Total Questions** | {total} | Test edilen toplam soru sayısı |",
            f"| **Correct Answers** | {correct} | Tamamen doğru kabul edilen cevaplar |",
            f"| **Accuracy** | %{acc:.2f} | Sistemin genel doğruluk oranı |",
            "",
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
            "Sistemin Vektör + Graf arama mimarisinin doğru bilgiyi ne kadar isabetli bulduğunu gösterir.",
            "",
            "| Metric | Score | Açıklama |",
            "|:---|:---|:---|",
            f"| **Hit@1** | %{hit1:.2f} | Doğru bilgi 1. sırada geldi |",
            f"| **Hit@3** | %{hit3:.2f} | Doğru bilgi ilk 3 sonuç içinde yer aldı |",
            f"| **Hit@5** | %{hit5:.2f} | Doğru bilgi ilk 5 sonuç içinde yer aldı |",
            f"| **MRR** | %{mrr:.2f} | Ortalama İlk Bulma Sırası (Mean Reciprocal Rank) |",
            "",
            "> 💡 **İpucu:** `Hit@K` oranlarınız yüksekse MESA'nın hafıza altyapısı kusursuz çalışıyor demektir. Sadece cevabı oluşturacak LLM modelinizi büyütmeniz sistemi mükemmel yapacaktır.",
            ""
        ]
                
        report_path = f"report_{self.run_id}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        return report_path
