from Anthropic import check_anthropic, pretty_print_anthropic_keys
from Deepseek import check_whale, pretty_print_deepseek_keys
from IO import IO
from OpenAI import get_oai_model, get_oai_key_attribs, get_oai_org, pretty_print_oai_keys, clone_key
from AI21 import check_ai21, pretty_print_ai21_keys
from MakerSuite import check_makersuite, pretty_print_makersuite_keys
from Azure import check_azure, pretty_print_azure_keys
from VertexAI import check_vertexai, pretty_print_vertexai_keys
from Mistral import check_mistral, pretty_print_mistral_keys
from OpenRouter import check_openrouter, pretty_print_openrouter_keys
from ElevenLabs import check_elevenlabs, pretty_print_elevenlabs_keys
from XAI import check_xai, pretty_print_xai_keys

from APIKey import APIKey, Provider, redact
import sys
import io
from datetime import datetime
import re
import argparse
import os.path
import asyncio
import aiohttp
import AWSAsync
import certifi
import ssl
import warnings

# ---------------------------------------------------------------------------
# Key classification — regex routing (shared by CLI and Web)
# ---------------------------------------------------------------------------

oai_regex = re.compile(r'(sk-[a-zA-Z0-9_-]+T3BlbkFJ[a-zA-Z0-9_-]+)')
anthropic_regex = re.compile(r'sk-ant-api03-[A-Za-z0-9\-_]{93}AA')
anthropic_secondary_regex = re.compile(r'sk-ant-[A-Za-z0-9\-_]{86}')
anthropic_third_regex = re.compile(r'sk-[A-Za-z0-9]{86}')
ai21_and_mistral_regex = re.compile('[A-Za-z0-9]{32}')
elevenlabs_regex = re.compile(r'([a-z0-9]{32})')
elevenlabs_secondary_regex = re.compile(r'sk_[a-z0-9]{48}')
makersuite_regex = re.compile(r'AIzaSy[A-Za-z0-9\-_]{33}')
aws_regex = re.compile(r'^(AKIA[0-9A-Z]{16}):([A-Za-z0-9+/]{40})$')
azure_regex = re.compile(r'^(.+):([a-z0-9]{32})$')
openrouter_regex = re.compile(r'sk-or-v1-[a-z0-9]{64}')
deepseek_regex = re.compile(r'sk-[a-f0-9]{32}')
xai_regex = re.compile(r'xai-[A-Za-z0-9]{80}')


def classify_key(raw_key, awslegacy=False):
    """
    Classify a raw key string into an APIKey object and return
    (kind, key_obj, validator_name) or None if the format is unrecognized.

    kind is always "async" — all validators are coroutines accepting
    (key_obj, sem). The awslegacy flag is accepted for backwards
    compatibility but ignored (the legacy boto3 checker was removed).
    """
    if '"' in raw_key[:1]:
        key = raw_key.strip('"')
        if not os.path.isfile(key):
            return None
        key_obj = APIKey(Provider.VERTEXAI, key)
        return ("async", key_obj, "validate_vertexai")

    if "sk-ant-" in raw_key[:7]:
        match = anthropic_regex.match(raw_key) if "ant-api03" in raw_key else anthropic_secondary_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.ANTHROPIC, raw_key)
        return ("async", key_obj, "validate_anthropic")

    if "AIzaSy" in raw_key[:6]:
        match = makersuite_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.MAKERSUITE, raw_key)
        return ("async", key_obj, "validate_makersuite")

    if "xai-" in raw_key[:4]:
        match = xai_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.XAI, raw_key)
        return ("async", key_obj, "validate_xai")

    if "sk-or-v1-" in raw_key:
        match = openrouter_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.OPENROUTER, raw_key)
        return ("async", key_obj, "validate_openrouter")

    if "sk-" in raw_key:
        anthropic_flag = "T3BlbkFJ" not in raw_key and len(raw_key) > 36
        deepseek_flag = len(raw_key) < 36
        if deepseek_flag:
            match = deepseek_regex.match(raw_key)
        elif anthropic_flag:
            match = anthropic_third_regex.match(raw_key)
        else:
            match = oai_regex.match(raw_key)
        if not match:
            return None
        if deepseek_flag:
            key_obj = APIKey(Provider.DEEPSEEK, raw_key)
            return ("async", key_obj, "validate_whale")
        elif anthropic_flag:
            key_obj = APIKey(Provider.ANTHROPIC, raw_key)
            return ("async", key_obj, "validate_anthropic")
        else:
            key_obj = APIKey(Provider.OPENAI, raw_key)
            return ("async", key_obj, "validate_openai")

    if ":" in raw_key and "AKIA" in raw_key:
        match = aws_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.AWS, raw_key)
        return ("async", key_obj, "validate_aws_async")

    if ":" in raw_key and "AKIA" not in raw_key:
        match = azure_regex.match(raw_key)
        if not match:
            return None
        key_obj = APIKey(Provider.AZURE, raw_key)
        return ("async", key_obj, "validate_azure")

    # ElevenLabs / AI21 / Mistral
    if "sk_" in raw_key[:3]:
        match = elevenlabs_secondary_regex.match(raw_key)
    else:
        match = elevenlabs_regex.match(raw_key)
    if match:
        key_obj = APIKey(Provider.ELEVENLABS, raw_key)
        return ("async", key_obj, "validate_elevenlabs")

    match = ai21_and_mistral_regex.match(raw_key)
    if match:
        key_obj = APIKey(Provider.AI21, raw_key)
        return ("async", key_obj, "validate_ai21_and_mistral")

    return None


# ---------------------------------------------------------------------------
# KeyChecker — encapsulates all state for a single check job
# ---------------------------------------------------------------------------

class KeyChecker:
    def __init__(self, keys: set, options: dict = None):
        options = options or {}
        self.inputted_keys = keys
        self.api_keys = set()
        self.cloned_keys = set()

        self.awslegacy = options.get('awslegacy', False)
        if self.awslegacy:
            warnings.warn("-awslegacy is deprecated and ignored: the legacy boto3 AWS checker "
                          "was removed; the async AWS checker is always used.", DeprecationWarning)
        self.verifyorg = options.get('verifyorg', False)
        self.proxyoutput = options.get('proxyoutput', False)
        self.verbose = options.get('verbose', False)
        self.progress_callback = options.get('progress_callback', None)

        # Per-instance concurrency primitives
        self.concurrent_connections = asyncio.Semaphore(1500)
        self.makersuite_semaphore = asyncio.Semaphore(50)
        self.deepseek_semaphore = asyncio.Semaphore(50)

    def _print(self, message):
        """Progress output — routes through IO callback or print."""
        if self.verbose:
            if IO._progress_callback:
                IO._progress_callback(message)
            else:
                print(message)

    # -- Shared session factory ----------------------------------------------

    _DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)

    def _make_session(self, aws: bool = False) -> aiohttp.ClientSession:
        """One session per validate_keys() run, shared by all keys.

        Fixes the previous pattern of one ClientSession per key (expensive
        TCP/TLS setup each time) with no timeout anywhere (a hung provider
        would stall a key forever while holding a semaphore slot).
        """
        if aws:
            # AWSAsync relies on certifi's CA bundle for SigV4 endpoints
            connector = aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
            return aiohttp.ClientSession(timeout=self._DEFAULT_TIMEOUT, connector=connector)
        return aiohttp.ClientSession(timeout=self._DEFAULT_TIMEOUT)

    # -- Async validators --------------------------------------------------
    # All take (key, sem, session) with a caller-managed shared session.

    async def validate_openai(self, key: APIKey, sem, session):
        retries = 10
        async with sem:
            self._print(f"Checking OpenAI key: {redact(key.api_key)}")
            if await get_oai_model(key, session, retries) is None:
                self._print(f"Invalid OpenAI key: {redact(key.api_key)}")
                return
            if await get_oai_key_attribs(key, session, retries, check_id=self.verifyorg) is None:
                return
            if await get_oai_org(key, session, retries) is None:
                return
            self._print(f"OpenAI key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)
            cloner = await clone_key(key, session, retries)
            if cloner:
                self.cloned_keys.update(cloner)
            if self.cloned_keys:
                self._print(f"Cloned OpenAI key '{redact(key.api_key)}'")
                self.api_keys.update(self.cloned_keys)

    async def validate_anthropic(self, key: APIKey, sem, session):
        retry_count = 20
        async with sem:
            self._print(f"Checking Anthropic key: {redact(key.api_key)}")
            key_status = await check_anthropic(key, session)
            if key_status is None:
                self._print(f"Invalid Anthropic key: {redact(key.api_key)}")
                return
            elif key_status is False:
                i = 0
                while await check_anthropic(key, session) is False and i < retry_count:
                    i += 1
                    await asyncio.sleep(1)
                    self._print(f"Stuck determining pozzed status of rate limited Anthropic key '{key.api_key[-8:]}' - attempt {i} of {retry_count}")
                    key.rate_limited = True
                else:
                    if i < retry_count:
                        key.rate_limited = False
            self._print(f"Anthropic key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)

    async def validate_ai21_and_mistral(self, key: APIKey, sem, session):
        async with sem:
            self._print(f"Checking AI21 key: {redact(key.api_key)}")
            if await check_ai21(key, session) is None:
                self._print(f"Invalid AI21 key: {redact(key.api_key)}, checking provider Mistral")
                key.provider = Provider.MISTRAL
                if await check_mistral(key, session) is None:
                    self._print(f"Invalid Mistral key: {redact(key.api_key)}")
                    return
            self._print(f"{'AI21' if key.provider == Provider.AI21 else 'Mistral'} key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)

    async def _simple_validate(self, check_fn, key: APIKey, sem, label, session, extra_sem=None):
        """Generic check→add validator shared by providers with identical shape."""
        if extra_sem is not None:
            async with sem, extra_sem:
                return await self._check_and_add(check_fn, key, label, session)
        async with sem:
            return await self._check_and_add(check_fn, key, label, session)

    async def _check_and_add(self, check_fn, key: APIKey, label, session):
        self._print(f"Checking {label} key: {redact(key.api_key)}")
        if await check_fn(key, session) is None:
            self._print(f"Invalid {label} key: {redact(key.api_key)}")
            return
        self._print(f"{label} key '{redact(key.api_key)}' is valid")
        self.api_keys.add(key)

    async def validate_makersuite(self, key: APIKey, sem, session):
        await self._simple_validate(check_makersuite, key, sem, "MakerSuite", session)

    async def validate_openrouter(self, key: APIKey, sem, session):
        await self._simple_validate(check_openrouter, key, sem, "OpenRouter", session)

    async def validate_elevenlabs(self, key: APIKey, sem, session):
        await self._simple_validate(check_elevenlabs, key, sem, "ElevenLabs", session)

    async def validate_xai(self, key: APIKey, sem, session):
        await self._simple_validate(check_xai, key, sem, "xAI", session)

    async def validate_vertexai(self, key: APIKey, sem, session):
        await self._simple_validate(check_vertexai, key, sem, "VertexAI keyfile", session)

    async def validate_aws_async(self, key: APIKey, sem, session):
        async with sem:
            self._print(f"Checking AWS key asynchronously: {redact(key.api_key)}")
            if await AWSAsync.check_aws(key, session) is None:
                self._print(f"Invalid AWS key: {redact(key.api_key)}")
                return
            self._print(f"AWS key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)

    async def validate_whale(self, key: APIKey, sem, session):
        retry_count = 4
        async with sem, self.deepseek_semaphore:
            self._print(f"Checking Deepseek key: {redact(key.api_key)}")
            key_status = await check_whale(key, session)
            if key_status is None:
                self._print(f"Invalid Deepseek key: {redact(key.api_key)}")
                return
            elif key_status is False:
                i = 0
                while await check_whale(key, session) is False and i < retry_count:
                    i += 1
                    await asyncio.sleep(2)
                    self._print(f"Stuck determining status of rate limited Deepseek key '{key.api_key[-8:]}' - attempt {i} of {retry_count}")
                    key.rate_limited = True
                else:
                    if i < retry_count:
                        key.rate_limited = False
            self._print(f"Deepseek key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)

    async def validate_azure(self, key: APIKey, sem, session):
        async with sem:
            self._print(f"Checking Azure key: {redact(key.api_key)}")
            if await check_azure(key, session) is None:
                self._print(f"Invalid Azure key: {redact(key.api_key)}")
                return
            self._print(f"Azure key '{redact(key.api_key)}' is valid")
            self.api_keys.add(key)

    # -- Retry wrapper ------------------------------------------------------

    async def execute_with_retries(self, func, key, sem, retries, session):
        attempt = 0
        while attempt < retries:
            try:
                return await func(key, sem, session)
            except (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
                attempt += 1
                self._print(f"Attempt {attempt}/{retries} failed for {redact(key.api_key)}: {str(e)}")
                if attempt < retries:
                    self._print(f"Retrying after 5 seconds...")
                    await asyncio.sleep(5)
                else:
                    self._print(f"Failed to validate key {redact(key.api_key)} after {retries} attempts.")
            except Exception as e:
                self._print(f"Unexpected error occurred for {redact(key.api_key)}: {str(e)}")
                break

    # -- Main validation orchestrator --------------------------------------

    async def validate_keys(self):
        tasks = []

        validator_map = {
            "validate_openai": self.validate_openai,
            "validate_anthropic": self.validate_anthropic,
            "validate_ai21_and_mistral": self.validate_ai21_and_mistral,
            "validate_makersuite": self.validate_makersuite,
            "validate_openrouter": self.validate_openrouter,
            "validate_elevenlabs": self.validate_elevenlabs,
            "validate_xai": self.validate_xai,
            "validate_aws_async": self.validate_aws_async,
            "validate_vertexai": self.validate_vertexai,
            "validate_whale": self.validate_whale,
            "validate_azure": self.validate_azure,
        }

        # One session per run for all providers, plus a dedicated certifi-SSL
        # session for AWS SigV4 endpoints.
        async with self._make_session() as session, self._make_session(aws=True) as aws_session:
            for raw_key in self.inputted_keys:
                result = classify_key(raw_key)
                if result is None:
                    continue

                kind, key_obj, validator_name = result
                validator = validator_map[validator_name]

                # Use makersuite_semaphore for MakerSuite, concurrent_connections otherwise
                sem = self.makersuite_semaphore if validator_name == "validate_makersuite" else self.concurrent_connections
                key_session = aws_session if validator_name == "validate_aws_async" else session
                tasks.append(self.execute_with_retries(validator, key_obj, sem, 5, key_session))

            results = await asyncio.gather(*tasks)
        for result in results:
            if result is not None:
                self.api_keys.add(result)

    # -- Results ---------------------------------------------------------------

    def get_results(self) -> dict:
        """Return structured JSON-serializable results for the web frontend."""
        valid_keys_by_provider = {}
        for key in self.api_keys:
            pname = key.provider.name
            if pname not in valid_keys_by_provider:
                valid_keys_by_provider[pname] = []
            valid_keys_by_provider[pname].append(key.to_dict())

        # Compute invalid keys
        valid_key_strings = set()
        for key in self.api_keys:
            if key.provider == Provider.VERTEXAI:
                valid_key_strings.add(f'"{key.api_key}"')
            else:
                valid_key_strings.add(key.api_key
                )
        for key in self.cloned_keys:
            valid_key_strings.add(key.api_key)

        invalid_keys = self.inputted_keys - valid_key_strings

        # Proxy output
        proxy_output = ""
        if self.proxyoutput:
            proxy_lines = []
            for pname, keys_list in valid_keys_by_provider.items():
                for k in keys_list:
                    if pname == "OPENAI" and k.get("has_quota"):
                        proxy_lines.append(f"OPENAI_KEY={k['api_key']}")
                    elif pname == "ANTHROPIC" and k.get("has_quota"):
                        proxy_lines.append(f"ANTHROPIC_KEY={k['api_key']}")
                    elif pname == "AWS" and not k.get("useless", True) and k.get("bedrock_enabled"):
                        regions = [k.get("region", "")] + k.get("alt_regions", [])
                        for region in regions:
                            proxy_lines.append(f"AWS_CREDENTIALS={k['api_key']}:{region}")
                    elif pname == "MAKERSUITE":
                        proxy_lines.append(f"GOOGLE_AI_KEY={k['api_key']}")
                    elif pname == "AZURE" and k.get("unfiltered"):
                        parts = k["api_key"].split(":")
                        if len(parts) == 2:
                            proxy_lines.append(f"AZURE_CREDENTIALS={parts[0]}:{k.get('best_deployment', '')}:{parts[1]}")
                    elif pname == "MISTRAL":
                        proxy_lines.append(f"MISTRAL_AI_KEY={k['api_key']}")

            # Merge same provider keys
            merged = {}
            for line in proxy_lines:
                key_name, _, val = line.partition("=")
                if key_name in merged:
                    merged[key_name] += "," + val
                else:
                    merged[key_name] = val
            proxy_output = "\n".join(f"{k}={v}" for k, v in merged.items())

        return {
            "valid_keys": valid_keys_by_provider,
            "invalid_keys": sorted(invalid_keys),
            "summary": {
                "total_input": len(self.inputted_keys),
                "total_valid": len(self.api_keys),
                "total_invalid": len(invalid_keys),
                "cloned_keys": len(self.cloned_keys),
            },
            "proxy_output": proxy_output,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_pretty_output(self) -> str:
        """Capture pretty_print stdout output as a string (for CLI text view)."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf

        try:
            invalid_keys_count = len(self.inputted_keys) - len(self.api_keys) + len(self.cloned_keys) if self.cloned_keys else len(self.inputted_keys) - len(self.api_keys)
            print("#" * 90)
            print(f"Key snapshot from {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("#" * 90)
            print(f'\n--- Checked {len(self.inputted_keys)} keys | {invalid_keys_count} were invalid ---')

            # Group keys by provider
            groups = {}
            for key in self.api_keys:
                pname = key.provider
                if pname not in groups:
                    groups[pname] = []
                groups[pname].append(key)

            # Print invalid keys
            valid_key_strings = set()
            for key in self.api_keys:
                if key.provider == Provider.VERTEXAI:
                    valid_key_strings.add(f'"{key.api_key}"')
                else:
                    valid_key_strings.add(key.api_key)
            for key in self.cloned_keys:
                valid_key_strings.add(key.api_key)
            invalid = self.inputted_keys - valid_key_strings
            if invalid:
                print('\nInvalid Keys:')
                for k in sorted(invalid):
                    print(k)

            print()
            # Pretty print in order
            if Provider.OPENAI in groups:
                pretty_print_oai_keys(groups[Provider.OPENAI], self.cloned_keys)
            if Provider.ANTHROPIC in groups:
                pretty_print_anthropic_keys(groups[Provider.ANTHROPIC])
            if Provider.AI21 in groups:
                pretty_print_ai21_keys(groups[Provider.AI21])
            if Provider.MAKERSUITE in groups:
                pretty_print_makersuite_keys(groups[Provider.MAKERSUITE])
            if Provider.AWS in groups:
                AWSAsync.pretty_print_aws_keys(groups[Provider.AWS])
            if Provider.AZURE in groups:
                pretty_print_azure_keys(groups[Provider.AZURE])
            if Provider.VERTEXAI in groups:
                pretty_print_vertexai_keys(groups[Provider.VERTEXAI])
            if Provider.MISTRAL in groups:
                pretty_print_mistral_keys(groups[Provider.MISTRAL])
            if Provider.OPENROUTER in groups:
                pretty_print_openrouter_keys(groups[Provider.OPENROUTER])
            if Provider.ELEVENLABS in groups:
                pretty_print_elevenlabs_keys(groups[Provider.ELEVENLABS])
            if Provider.DEEPSEEK in groups:
                pretty_print_deepseek_keys(groups[Provider.DEEPSEEK])
            if Provider.XAI in groups:
                pretty_print_xai_keys(groups[Provider.XAI])
        finally:
            sys.stdout = old_stdout

        return buf.getvalue()

    def render_proxy_output(self) -> str:
        """Proxy env-format output (KEY=val lines, same-provider values comma-merged).

        Single source of truth — previously duplicated in output_cli() (dead
        code) and the __main__ block.
        """
        valid_oai_keys = [k for k in self.api_keys if k.provider == Provider.OPENAI]
        valid_anthropic_keys = [k for k in self.api_keys if k.provider == Provider.ANTHROPIC]
        valid_aws_keys = [k for k in self.api_keys if k.provider == Provider.AWS]
        valid_makersuite_keys = [k for k in self.api_keys if k.provider == Provider.MAKERSUITE]
        valid_azure_keys = [k for k in self.api_keys if k.provider == Provider.AZURE]
        valid_mistral_keys = [k for k in self.api_keys if k.provider == Provider.MISTRAL]

        lines = [
            "OPENAI_KEY=" + ','.join(key.api_key for key in valid_oai_keys if key.has_quota),
            "ANTHROPIC_KEY=" + ','.join(key.api_key for key in valid_anthropic_keys if key.has_quota),
            "AWS_CREDENTIALS=" + ','.join(f"{key.api_key}:{region}" for key in valid_aws_keys if not key.useless and key.bedrock_enabled for region in [key.region] + key.alt_regions),
            "GOOGLE_AI_KEY=" + ','.join(key.api_key for key in valid_makersuite_keys),
            "AZURE_CREDENTIALS=" + ','.join(f"{key.api_key.split(':')[0]}:{key.best_deployment}:{key.api_key.split(':')[1]}" for key in valid_azure_keys if key.unfiltered),
            "MISTRAL_AI_KEY=" + ','.join(key.api_key for key in valid_mistral_keys),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point (preserved from original)
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description='slop checker')
    parser.add_argument('-nooutput', '--nooutput', action='store_true', help='stop writing slop to a file')
    parser.add_argument('-proxyoutput', '--proxyoutput', action='store_true', help='proxy format output for easy copying')
    parser.add_argument('-file', '--file', action='store', dest='file', help='read slop from a provided filename')
    parser.add_argument('-verbose', '--verbose', action='store_true', help='watch as your slop is checked real time')
    parser.add_argument('-awslegacy', '--awslegacy', action='store_true', help='deprecated no-op: the legacy boto3 AWS checker was removed; the async checker is always used.')
    parser.add_argument('-verifyorg', '--verifyorg', action='store_true', help='sends a test prompt with o3 to detect if an openai organization has completed id verification.')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    inputted_keys = set()

    if args.file:
        inputted_keys = IO.read_keys_from_file(args.file)
        if inputted_keys is None:
            sys.exit(1)
    else:
        print('Enter API keys (OpenAI/Anthropic/AI21/MakerSuite/AWS/Azure/Mistral/Elevenlabs) one per line. Press Enter on a blank line to start validation')
        print('Expected format for AWS keys is accesskey:secret, for Azure keys it\'s resourcegroup:apikey. For Vertex AI keys the absolute path to the secrets key file is expected in quotes. "/path/to/secrets.json"')
        while True:
            current_line = input()
            if not current_line:
                print("Starting validation...")
                break
            inputted_keys.add(current_line.strip().split()[0].split(",")[0])

    # Handle -nooutput: still run but skip snapshot file
    checker = KeyChecker(inputted_keys, {
        'awslegacy': args.awslegacy,
        'verifyorg': args.verifyorg,
        'proxyoutput': args.proxyoutput,
        'verbose': args.verbose,
    })

    if args.nooutput:
        # Override: don't write snapshot file, just print to console
        should_write_snapshot = False
    else:
        should_write_snapshot = not args.proxyoutput

    start_time = datetime.now()
    asyncio.run(checker.validate_keys())

    if args.proxyoutput:
        print(checker.render_proxy_output())
    elif args.nooutput:
        # Print to stdout only, no snapshot file
        output = checker.get_pretty_output()
        print(output, end="")
    else:
        # Write to stdout + snapshot file
        output_filename = "key_snapshots.txt"
        original_stdout = sys.stdout
        sys.stdout = IO(output_filename)
        print(checker.get_pretty_output(), end="")
        sys.stdout.file.close()
        sys.stdout = original_stdout

    elapsed_time = datetime.now() - start_time
    minutes, seconds = divmod(elapsed_time.total_seconds(), 60)
    print(f"Finished checking {len(inputted_keys)} keys in {f'{int(minutes)}m ' if minutes else ''}{seconds:.2f}s")
