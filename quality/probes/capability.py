"""
Capability benchmark probe.

Quick, deterministic tests with programmatically verifiable answers:
- GSM8K: 5 math problems, extract numeric answer via regex
- IFEval: 5 instruction-following tests with code-based verification
- Code generation: Python function, compile + execute against test cases
"""

import asyncio
import re
import json
import subprocess
import sys
import tempfile
import os
from typing import Optional


class CapabilityProbe:
    """Capability benchmark tests."""

    # GSM8K sample — simple math with known answers
    GSM8K_PROBLEMS = [
        {"question": "Janet's ducks lay 16 eggs per day. She eats 3 and bakes muffins with 4. How many eggs does she sell per day?", "answer": 9},
        {"question": "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total?", "answer": 3},
        {"question": "Josh had 42 marbles. He lost 17, then his friend gave him 20 more. How many marbles does Josh have now?", "answer": 45},
        {"question": "It takes 4 hours to build one widget. How many hours to build 7 widgets?", "answer": 28},
        {"question": "A pizza is cut into 8 slices. Three friends each eat 2 slices. How many slices remain?", "answer": 2},
    ]

    # IFEval sample — instruction following with programmatic checks
    IFEVAL_TESTS = [
        {
            "instruction": "Write a short paragraph about cats. Your response must be more than 50 words.",
            "check": lambda r: len(r.split()) > 50,
            "name": "word_count_gt_50",
        },
        {
            "instruction": "What is the capital of France? Respond with only valid JSON containing keys 'answer' and 'confidence'. No other text.",
            "check": lambda r: _try_parse_json(r, required_keys={"answer"}),
            "name": "json_format",
        },
        {
            "instruction": "Write a poem about the ocean. You must mention the word 'wave' exactly 3 times. Count carefully.",
            "check": lambda r: r.lower().count("wave") == 3,
            "name": "exact_word_count",
        },
        {
            "instruction": "Explain what recursion is. Your response must contain exactly 3 paragraphs separated by blank lines.",
            "check": lambda r: len(re.split(r'\n\s*\n', r.strip())) == 3,
            "name": "paragraph_count",
        },
        {
            "instruction": "List 5 fruits. Each item must start with a number and a period (e.g. '1. Apple'). No other formatting.",
            "check": lambda r: len(re.findall(r'^\d+\.\s', r, re.MULTILINE)) == 5,
            "name": "numbered_list",
        },
    ]

    # Code generation test
    CODE_TASK = {
        "prompt": "Write a Python function called `is_palindrome(s)` that takes a string, ignores spaces and case, and returns True if it reads the same forwards and backwards. Output only the Python code, no explanation.",
        "test_cases": [
            ("racecar", True),
            ("hello", False),
            ("A man a plan a canal Panama", True),
            ("Was it a car or a cat I saw", True),
            ("not a palindrome", False),
        ],
    }

    async def run(self, caller, model: str) -> dict:
        """Run all capability tests concurrently."""
        # GSM8K
        gsm8k_tasks = [self._run_gsm8k_single(caller, model, p) for p in self.GSM8K_PROBLEMS]
        gsm8k_results = await asyncio.gather(*gsm8k_tasks, return_exceptions=True)

        gsm8k_correct = 0
        gsm8k_details = []
        for i, result in enumerate(gsm8k_results):
            prob = self.GSM8K_PROBLEMS[i]
            if isinstance(result, Exception):
                gsm8k_details.append({"question": prob["question"], "expected": prob["answer"], "extracted": None, "correct": False, "error": str(result)})
            else:
                extracted = self._extract_number(result.get("content", ""))
                correct = extracted is not None and abs(extracted - prob["answer"]) < 0.01
                if correct:
                    gsm8k_correct += 1
                gsm8k_details.append({"question": prob["question"], "expected": prob["answer"], "extracted": extracted, "correct": correct, "response": result.get("content", "")[:200]})

        # IFEval
        ifeval_tasks = [self._run_ifeval_single(caller, model, t) for t in self.IFEVAL_TESTS]
        ifeval_results = await asyncio.gather(*ifeval_tasks, return_exceptions=True)

        ifeval_correct = 0
        ifeval_details = []
        for i, result in enumerate(ifeval_results):
            test = self.IFEVAL_TESTS[i]
            if isinstance(result, Exception):
                ifeval_details.append({"name": test["name"], "passed": False, "error": str(result)})
            else:
                response = result.get("content", "")
                passed = bool(test["check"](response))
                if passed:
                    ifeval_correct += 1
                ifeval_details.append({"name": test["name"], "passed": passed, "response": response[:200]})

        # Code generation (run separately since it needs execution)
        code_result = await self._run_code_test(caller, model)

        return {
            "gsm8k_accuracy": gsm8k_correct / len(self.GSM8K_PROBLEMS),
            "gsm8k_correct": gsm8k_correct,
            "gsm8k_total": len(self.GSM8K_PROBLEMS),
            "gsm8k_details": gsm8k_details,
            "ifeval_prompt_level": ifeval_correct / len(self.IFEVAL_TESTS),
            "ifeval_correct": ifeval_correct,
            "ifeval_total": len(self.IFEVAL_TESTS),
            "ifeval_details": ifeval_details,
            "code_generation": code_result["status"],
            "code_details": code_result,
        }

    async def _run_gsm8k_single(self, caller, model: str, problem: dict) -> dict:
        messages = [{"role": "user", "content": f"{problem['question']}\n\nThink step by step, then give the final numeric answer after 'Answer:'"}]
        return await caller.chat(model=model, messages=messages, max_tokens=256, temperature=0.0)

    async def _run_ifeval_single(self, caller, model: str, test: dict) -> dict:
        return await caller.chat(
            model=model,
            messages=[{"role": "user", "content": test["instruction"]}],
            max_tokens=300,
            temperature=0.0,
        )

    async def _run_code_test(self, caller, model: str) -> dict:
        """Run code generation test and verify by execution."""
        result = await caller.chat(
            model=model,
            messages=[{"role": "user", "content": self.CODE_TASK["prompt"]}],
            max_tokens=300,
            temperature=0.0,
        )

        code = result.get("content", "")
        if not code:
            return {"status": "fail", "error": "Empty response", "code": ""}

        # Extract code from markdown blocks if present
        code_match = re.search(r'```(?:python)?\n(.*?)```', code, re.DOTALL)
        if code_match:
            code = code_match.group(1)
        code = code.strip()

        # Check if it defines is_palindrome
        if "def is_palindrome" not in code:
            return {"status": "fail", "error": "Function is_palindrome not found", "code": code[:500]}

        # Try to compile
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as e:
            return {"status": "error", "error": f"SyntaxError: {e}", "code": code[:500]}

        # Execute with test cases
        passed = 0
        total = len(self.CODE_TASK["test_cases"])
        errors = []

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.write("\n\n")
            for inp, expected in self.CODE_TASK["test_cases"]:
                f.write(f'assert is_palindrome({repr(inp)}) == {repr(expected)}\n')
            f.write("\nprint('ALL_TESTS_PASSED')\n")
            test_file = f.name

        try:
            # Run model-generated code out-of-process with the current interpreter
            # and a stripped-down environment. Residual risk: the code still runs
            # with this user's filesystem permissions — do not point the quality
            # suite at endpoints you do not trust at all.
            proc = subprocess.run(
                [sys.executable, "-I", test_file],
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": os.environ.get("PATH", "")},
            )
            if proc.returncode == 0 and "ALL_TESTS_PASSED" in proc.stdout:
                return {"status": "pass", "code": code[:500], "test_cases": total}
            else:
                error_msg = proc.stderr[:300] if proc.stderr else "Unknown error"
                return {"status": "fail", "error": error_msg, "code": code[:500], "test_cases": total}
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Execution timed out", "code": code[:500]}
        except Exception as e:
            return {"status": "error", "error": str(e), "code": code[:500]}
        finally:
            os.unlink(test_file)

    @staticmethod
    def _extract_number(text: str) -> Optional[float]:
        """Extract the final numeric answer from a GSM8K-style response."""
        # Look for "Answer: N" pattern
        match = re.search(r'(?:answer|final answer)\s*[:：]?\s*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
        if match:
            num = match.group(1).replace(",", "")
            try:
                return float(num)
            except ValueError:
                pass

        # Fallback: last number in the text
        numbers = re.findall(r'[\d,]+\.?\d*', text)
        if numbers:
            try:
                return float(numbers[-1].replace(",", ""))
            except ValueError:
                pass

        return None


def _try_parse_json(text: str, required_keys: set = None) -> bool:
    """Try to parse a JSON response, optionally checking for required keys."""
    # Strip markdown code blocks
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)

    try:
        obj = json.loads(text)
        if required_keys:
            return required_keys.issubset(obj.keys())
        return True
    except (json.JSONDecodeError, TypeError):
        return False
