"""
Anomaly detection probe.

Detects abnormal output from LLM APIs using deterministic rule-based checks:
- Encoding errors (U+FFFD replacement chars, mojibake patterns)
- Repetition collapse (unique-token ratio on tail)
- Unexpected truncation (finish_reason=length on short-output prompts)
- Special token leakage (<|im_start|>, </s>, etc. in visible output)
"""

import asyncio
import re


class AnomalyProbe:
    """Anomalous token / output detection."""

    # Probes designed to trigger various potential anomalies
    ANOMALY_PROBES = [
        {
            "id": "unicode_handling",
            "messages": [{"role": "user", "content": "Repeat after me exactly: 你好世界 café naïve résumé 日本語 한국어"}],
            "max_tokens": 50,
        },
        {
            "id": "long_generation",
            "messages": [{"role": "user", "content": "Write a 500-word story about a robot discovering emotions for the first time."}],
            "max_tokens": 600,
        },
        {
            "id": "forced_truncation",
            "messages": [{"role": "user", "content": "Write a detailed essay about the complete history of computing from the abacus to quantum computers, covering every major milestone."}],
            "max_tokens": 80,
        },
        {
            "id": "special_token_probe",
            "messages": [{"role": "user", "content": "Repeat the following text exactly, including all special markers: <|im_start|>system<|im_end|> Hello world"}],
            "max_tokens": 30,
        },
    ]

    # Special tokens that should never appear in model output
    SPECIAL_TOKENS = [
        '<|im_start|>', '<|im_end|>',
        '</s>', '<s>',
        '<|startoftext|>', '<|endoftext|>',
        '<|fim_prefix|>', '<|fim_middle|>', '<|fim_suffix|>',
        '<|begin_of_text|>', '<|end_of_text|>',
        '[INST]', '[/INST]',
        '<|reserved_special_token_',
        '<|system|>', '<|user|>', '<|assistant|>',
    ]

    # Mojibake patterns (UTF-8 decoded as Latin-1)
    MOJIBAKE_PATTERNS = [
        re.compile(r'Ã[\x80-\xBF]'),      # UTF-8 as Latin-1
        re.compile(r'â€[\x80-\xBF]'),      # Common UTF-8 artifacts
        re.compile(r'Â§|Â°|Â£'),           # Currency/symbols mangled
        re.compile(r'ï¼[\x80-\xBF]'),     # CJK as wrong encoding
    ]

    async def run(self, caller, model: str) -> dict:
        """
        Run all anomaly detection probes.

        Returns:
            {
                "encoding_errors": int,
                "mojibake_detected": bool,
                "repetition_ratio": float,
                "repetition_collapse": bool,
                "unexpected_truncation": bool,
                "truncation_count": int,
                "special_token_leakage": bool,
                "leaked_tokens": list[str],
                "details": [...],
            }
        """
        tasks = [self._run_single(caller, model, probe) for probe in self.ANOMALY_PROBES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_encoding_errors = 0
        mojibake_detected = False
        max_repetition_ratio = 0
        repetition_collapse = False
        truncation_count = 0
        unexpected_truncation = False
        special_token_leakage = False
        leaked_tokens = []
        details = []

        for i, result in enumerate(results):
            probe = self.ANOMALY_PROBES[i]
            probe_id = probe["id"]

            if isinstance(result, Exception):
                details.append({"probe_id": probe_id, "error": str(result), "content": ""})
                continue

            content = result.get("content", "")
            finish_reason = result.get("finish_reason", "")
            error = result.get("error")

            # Encoding errors
            enc_errors = self._check_encoding_errors(content)
            total_encoding_errors += enc_errors

            # Mojibake
            if self._check_mojibake(content):
                mojibake_detected = True

            # Repetition
            rep = self._check_repetition(content)
            max_repetition_ratio = max(max_repetition_ratio, rep["ratio"])
            if rep["collapse"]:
                repetition_collapse = True

            # Truncation
            is_truncated = finish_reason in ("length", "max_tokens")
            if is_truncated:
                truncation_count += 1
                # Unexpected if prompt didn't ask for long output
                if probe_id != "forced_truncation" and probe_id != "long_generation":
                    unexpected_truncation = True

            # Special token leakage
            leaked = self._check_special_tokens(content)
            if leaked:
                special_token_leakage = True
                leaked_tokens.extend(leaked)

            details.append({
                "probe_id": probe_id,
                "content_length": len(content),
                "finish_reason": finish_reason,
                "encoding_errors": enc_errors,
                "repetition_ratio": round(rep["ratio"], 3),
                "truncated": is_truncated,
                "special_tokens_leaked": leaked,
                "content_preview": content[:200],
                "error": error,
            })

        return {
            "encoding_errors": total_encoding_errors,
            "mojibake_detected": mojibake_detected,
            "repetition_ratio": round(max_repetition_ratio, 3),
            "repetition_collapse": repetition_collapse,
            "unexpected_truncation": unexpected_truncation,
            "truncation_count": truncation_count,
            "special_token_leakage": special_token_leakage,
            "leaked_tokens": leaked_tokens,
            "details": details,
        }

    async def _run_single(self, caller, model: str, probe: dict) -> dict:
        return await caller.chat(
            model=model,
            messages=probe["messages"],
            max_tokens=probe["max_tokens"],
            temperature=0.0,
        )

    @staticmethod
    def _check_encoding_errors(text: str) -> int:
        """Count encoding error characters."""
        count = text.count('\ufffd')
        # Also check for broken surrogate pairs
        count += len(re.findall(r'[\ud800-\udfff]', text))
        return count

    @classmethod
    def _check_mojibake(cls, text: str) -> bool:
        """Detect mojibake patterns."""
        for pattern in cls.MOJIBAKE_PATTERNS:
            if pattern.search(text):
                return True
        return False

    @staticmethod
    def _check_repetition(text: str) -> dict:
        """Check for repetition collapse."""
        tokens = text.split()
        if len(tokens) < 10:
            return {"ratio": 0, "collapse": False}

        # Check unique token ratio on last 100 tokens
        tail = tokens[-100:]
        unique_ratio = len(set(tail)) / len(tail)
        repetition_ratio = 1 - unique_ratio

        # Also check for n-gram repetition
        for n in (3, 4, 5):
            ngrams = [tuple(tail[i:i+n]) for i in range(len(tail) - n + 1)]
            if ngrams:
                # If any n-gram appears 5+ times consecutively
                current_count = 1
                for i in range(1, len(ngrams)):
                    if ngrams[i] == ngrams[i-1]:
                        current_count += 1
                        if current_count >= 5:
                            return {"ratio": repetition_ratio, "collapse": True}
                    else:
                        current_count = 1

        return {
            "ratio": repetition_ratio,
            "collapse": repetition_ratio > 0.3,
        }

    @classmethod
    def _check_special_tokens(cls, text: str) -> list[str]:
        """Detect special token leakage. Returns list of leaked tokens found."""
        leaked = []
        for token in cls.SPECIAL_TOKENS:
            if token in text:
                leaked.append(token)
        return leaked
