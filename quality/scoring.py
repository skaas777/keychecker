"""
Scoring aggregation — combines probe results into overall quality assessment.

Score range: 0-100
Status: PASS (>=80) | SUSPICIOUS (50-79) | FAIL (<50)
"""

from typing import Optional


class Scoring:
    """Aggregate probe results into overall quality score."""

    def evaluate(self, probe_results: dict) -> dict:
        """
        Evaluate all probe results and produce overall assessment.

        Args:
            probe_results: dict with keys 'authenticity', 'capability', 'latency', 'anomalies'

        Returns:
            {
                "status": "PASS" | "SUSPICIOUS" | "FAIL",
                "score": int,
                "flags": list[str],
                "summary": str,
            }
        """
        flags = []
        score = 100

        # --- Model authenticity ---
        if "authenticity" in probe_results:
            auth = probe_results["authenticity"]
            if auth.get("status") == "FAIL":
                score -= 40
                flags.append(f"模型真伪检测失败 — 可能不是宣传的模型 (置信度: {auth.get('confidence', 0):.0%})")
            elif auth.get("status") == "SUSPICIOUS":
                score -= 20
                flags.append(f"模型真伪存疑 (置信度: {auth.get('confidence', 0):.0%})")

            predicted = auth.get("predicted_vendor", "unknown")
            if predicted == "uncensored":
                score -= 15
                flags.append("检测到未审查/安全过滤被移除")

        # --- Capability ---
        if "capability" in probe_results:
            cap = probe_results["capability"]

            gsm8k = cap.get("gsm8k_accuracy", 0)
            if gsm8k < 0.4:
                score -= 20
                flags.append(f"数学能力极低: GSM8K {gsm8k:.0%}")
            elif gsm8k < 0.6:
                score -= 10
                flags.append(f"数学能力偏低: GSM8K {gsm8k:.0%}")

            ifeval = cap.get("ifeval_prompt_level", 0)
            if ifeval < 0.4:
                score -= 15
                flags.append(f"指令遵循能力低: IFEval {ifeval:.0%}")
            elif ifeval < 0.6:
                score -= 8
                flags.append(f"指令遵循能力偏低: IFEval {ifeval:.0%}")

            code_status = cap.get("code_generation", "error")
            if code_status == "fail":
                score -= 10
                flags.append("代码生成测试未通过")
            elif code_status == "error":
                score -= 5
                flags.append("代码生成执行出错")

        # --- Latency ---
        if "latency" in probe_results:
            lat = probe_results["latency"]
            ttft = lat.get("ttft_ms", {})
            ttft_p50 = ttft.get("p50", 0)

            if ttft_p50 > 3000:
                score -= 15
                flags.append(f"首字延迟极高: P50={ttft_p50:.0f}ms")
            elif ttft_p50 > 1500:
                score -= 8
                flags.append(f"首字延迟偏高: P50={ttft_p50:.0f}ms")

            tps = lat.get("tps", {})
            tps_p50 = tps.get("p50", 0)
            if tps_p50 > 0 and tps_p50 < 20:
                score -= 15
                flags.append(f"生成速度极低: P50={tps_p50:.0f} TPS")
            elif tps_p50 > 0 and tps_p50 < 40:
                score -= 8
                flags.append(f"生成速度偏低: P50={tps_p50:.0f} TPS")

            # Variance check: high p99/p50 ratio = unstable
            ttft_p99 = ttft.get("p99", 0)
            if ttft_p50 > 0 and ttft_p99 / ttft_p50 > 5:
                score -= 10
                flags.append(f"延迟不稳定: TTFT P99/P50={ttft_p99/ttft_p50:.1f}x")

            error_rate = lat.get("error_rate", 0)
            if error_rate > 0.1:
                score -= 10
                flags.append(f"错误率过高: {error_rate:.0%}")
            elif error_rate > 0.05:
                score -= 5
                flags.append(f"错误率偏高: {error_rate:.0%}")

        # --- Anomalies ---
        if "anomalies" in probe_results:
            anom = probe_results["anomalies"]

            if anom.get("encoding_errors", 0) > 0:
                score -= 20
                flags.append(f"编码错误: {anom['encoding_errors']} 个")

            if anom.get("mojibake_detected"):
                score -= 10
                flags.append("检测到 Mojibake (编码损坏)")

            if anom.get("repetition_collapse"):
                score -= 25
                flags.append("检测到重复崩溃")
            elif anom.get("repetition_ratio", 0) > 0.2:
                score -= 5
                flags.append(f"重复率偏高: {anom['repetition_ratio']:.0%}")

            if anom.get("special_token_leakage"):
                score -= 15
                leaked = anom.get("leaked_tokens", [])
                flags.append(f"特殊 Token 泄露: {', '.join(leaked[:3])}")

            if anom.get("unexpected_truncation"):
                score -= 10
                flags.append("检测到意外截断")

        score = max(0, int(score))

        if score >= 80:
            status = "PASS"
            summary = f"通过 (得分: {score}/100)"
        elif score >= 50:
            status = "SUSPICIOUS"
            summary = f"存疑 (得分: {score}/100, {len(flags)} 个警告)"
        else:
            status = "FAIL"
            summary = f"失败 (得分: {score}/100, {len(flags)} 个警告)"

        return {
            "status": status,
            "score": score,
            "flags": flags,
            "summary": summary,
        }
