"""Mechanism 3: Evidence-Preserving Reducer (Delegated Reading with Fail-Open Verifier).

Implements arXiv:2609.20519 Section 3.3:
- Extracts diagnostic receipts from massive logs (>= 4 KiB).
- Supports deterministic extraction AND optional cheap-LLM delegation (Gemini Flash / OpenAI / Anthropic).
- Enforces a deterministic Fail-Open Verifier:
  1. Schema & Exit Code check.
  2. Strict Verbatim Substring Match: EVERY quote in `verbatim_quotes` must appear
     character-for-character inside the raw archived log.
  3. Secret & Credential leak scanner.
  4. Compression ratio gate: the receipt must be smaller than the original log.
- If ANY verification check fails, immediately falls back to safe raw/head-tail output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import load_config

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}\b"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    # whole PEM block, not just the header line
    re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----(?:.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----)?", re.S),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-\._~\+\/]{24,}=*"),
    # key=value / key: value for secret-looking names (only the value is replaced)
    re.compile(
        r"(?i)(?P<pre>(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)[=:]\s?)"
        r"(?!\[REDACTED)[^\s\"',;]{8,}"
    ),
]

DIAGNOSTIC_REGEX = re.compile(
    r"(?:error|fail|failed|failure|exception|traceback|fatal|panic|assert|AssertionError|"
    r"SyntaxError|TypeError|ValueError|KeyError|ImportError|ModuleNotFoundError|"
    r"ConnectionRefusedError|segfault|undefined reference|cannot find|ERR!|"
    # failures that use none of the usual words
    r"segmentation fault|core dumped|\bkilled\b|oomkilled|\boom\b|out of memory|timed out|time-?out|"
    r"permission denied|no such file|not found|cannot |can't |unable to|\bdenied\b|refused|aborted|"
    r"exit(?:ed)? (?:with )?(?:status|code) [1-9]|non-zero|\bERROR\b)",
    re.IGNORECASE,
)

LOCATION_REGEX = re.compile(
    r"(?:File \"([^\"]+)\", line (\d+)|([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)(?::\d+)?)"
)


# Paper (arXiv:2609.20519, Evidence-Preserving Reducer): only build/test logs "from a predefined set of
# commands" are reduced; file reads and search results bypass the reducer.
REDUCIBLE_CMD_REGEX = re.compile(
    r"(?:^|[\s;&|(])(?:pytest|py\.test|tox|nox|mypy|ruff|flake8|pylint|jest|vitest|mocha|eslint|tsc|"
    r"python3?\s+-m\s+(?:pytest|unittest|mypy|ruff|build|compileall)|"
    r"cargo\s+(?:test|check|build|clippy|nextest|bench)|rustc|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+(?:test|build|lint|typecheck|check)|install|ci)|"
    r"go\s+(?:test|build|vet)|mvn|gradle|gradlew|\./gradlew|dotnet\s+(?:build|test)|"
    r"make|cmake|ninja|bazel|gcc|g\+\+|clang\+*|javac|swift\s+(?:build|test)|"
    r"docker\s+build|pip3?\s+install|uv\s+(?:sync|pip))(?=$|[\s;&|)])"
)


def is_reducible_command(command: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True when `command` is a build/test/lint run whose log may go through the reducer.

    An empty command (unknown provenance) is treated as reducible. Extra commands can be added with
    `evidence_reducer.extra_command_regex` in `sol-pi.json`.
    """
    if not command or not command.strip():
        return True
    if REDUCIBLE_CMD_REGEX.search(command):
        return True
    extra = (cfg or {}).get("extra_command_regex")
    if extra:
        try:
            return bool(re.search(extra, command))
        except re.error:
            return False
    return False


@dataclass
class DiagnosticReceipt:
    source_hash: str
    exit_code: int
    root_cause_summary: str
    failing_locations: List[str] = field(default_factory=list)
    verbatim_quotes: List[str] = field(default_factory=list)
    producer: str = "deterministic"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "exit_code": self.exit_code,
            "root_cause_summary": self.root_cause_summary,
            "failing_locations": self.failing_locations,
            "verbatim_quotes": self.verbatim_quotes,
            "producer": self.producer,
        }


@dataclass
class VerificationResult:
    valid: bool
    reason: str
    secrets_found: int = 0


def _redact_match(m: "re.Match[str]") -> str:
    pre = m.groupdict().get("pre")
    return (pre or "") + "[REDACTED_SECRET]"


def scan_and_redact_secrets(text: str) -> Tuple[str, int]:
    """Scan for potential secrets/credentials and redact them."""
    count = 0
    redacted = text
    for pat in SECRET_PATTERNS:
        matches = pat.findall(redacted)
        if matches:
            count += len(matches)
            redacted = pat.sub(_redact_match, redacted)
    return redacted, count


def verify_receipt(
    raw_log: str,
    actual_exit_code: int,
    receipt: DiagnosticReceipt,
    *,
    strict_verbatim: bool = True,
    check_secrets: bool = True,
) -> VerificationResult:
    """Deterministic Verifier (Fail-Open Gate) from SoL-Pi.

    Returns `VerificationResult(valid=True, ...)` ONLY if:
    1. `source_hash` matches SHA-256 prefix of `raw_log`.
    2. `exit_code` matches `actual_exit_code`.
    3. Every quote in `verbatim_quotes` is a non-empty exact substring of `raw_log`.
    4. No unredacted secret patterns exist in the receipt.
    """
    expected_hash = hashlib.sha256(raw_log.encode("utf-8")).hexdigest()[:12]
    if receipt.source_hash != expected_hash:
        return VerificationResult(False, f"Hash mismatch: {receipt.source_hash} != {expected_hash}")

    if receipt.exit_code != actual_exit_code:
        return VerificationResult(False, f"Exit code mismatch: {receipt.exit_code} != {actual_exit_code}")

    if strict_verbatim:
        if not receipt.verbatim_quotes:
            return VerificationResult(False, "Empty verbatim_quotes list in receipt")
        for idx, quote in enumerate(receipt.verbatim_quotes):
            if not isinstance(quote, str) or not quote.strip():
                return VerificationResult(False, f"Quote #{idx} is empty or non-string")
            if quote not in raw_log:
                return VerificationResult(
                    False,
                    f"Hallucinated or altered quote #{idx} detected (not an exact substring of raw log): {quote[:60]!r}",
                )

    if check_secrets:
        serialized = json.dumps(receipt.to_dict())
        for pat in SECRET_PATTERNS:
            if pat.search(serialized):
                return VerificationResult(False, "Potential secret leak detected in receipt", secrets_found=1)

    return VerificationResult(True, "Verified")


MAX_QUOTE_CHARS = 400


def _quote_window(line: str) -> str:
    """A very long line is quoted as an exact substring around its first diagnostic keyword (still verbatim)."""
    if len(line) <= MAX_QUOTE_CHARS:
        return line
    m = DIAGNOSTIC_REGEX.search(line)
    start = max(0, (m.start() if m else 0) - MAX_QUOTE_CHARS // 3)
    return line[start:start + MAX_QUOTE_CHARS]


def build_deterministic_receipt(
    raw_log: str,
    exit_code: int,
    *,
    max_quotes: int = 30,
    context_radius: int = 1,
) -> DiagnosticReceipt:
    """Extracts 100% verbatim diagnostic quotes and file:line locations deterministically."""
    source_hash = hashlib.sha256(raw_log.encode("utf-8")).hexdigest()[:12]
    lines = raw_log.splitlines()

    # Extract file:line locations
    locations: List[str] = []
    seen_locs = set()
    for match in LOCATION_REGEX.finditer(raw_log):
        if match.group(1) and match.group(2):
            loc = f"{match.group(1)}:{match.group(2)}"
        else:
            loc = f"{match.group(3)}:{match.group(4)}"
        if loc not in seen_locs:
            seen_locs.add(loc)
            locations.append(loc)
            if len(locations) >= 15:
                break

    # Diagnostic (core) lines first; context lines only fill what is left of the quote budget, closest first.
    # Without this priority, the context of the first failures exhausts the budget and later failures vanish.
    core = [i for i, ln in enumerate(lines) if ln.strip() and DIAGNOSTIC_REGEX.search(ln)]
    if len(core) > max_quotes:
        head_n = -(-2 * max_quotes // 3)  # keep the first 2/3 (root causes) and the last 1/3 (summaries)
        core = core[:head_n] + core[len(core) - (max_quotes - head_n):]
    chosen = set(core)
    for dist in range(1, context_radius + 1):
        for idx in core:
            for c_idx in (idx - dist, idx + dist):
                if len(chosen) >= max_quotes:
                    break
                if 0 <= c_idx < len(lines) and lines[c_idx].strip():
                    chosen.add(c_idx)
    verbatim_quotes: List[str] = [_quote_window(lines[i]) for i in sorted(chosen)]

    # Fallback if no diagnostic keyword matched (e.g. successful long test output)
    if not verbatim_quotes and lines:
        non_empty = [ln for ln in lines if ln.strip()]
        verbatim_quotes = non_empty[-min(8, len(non_empty)) :]

    status_word = "SUCCESS" if exit_code == 0 else f"FAILED (exit {exit_code})"
    summary = (
        f"Command {status_word} across {len(lines)} lines ({len(raw_log)} bytes); "
        f"{len(locations)} file location(s) and {len(verbatim_quotes)} verbatim diagnostic line(s) extracted."
    )

    return DiagnosticReceipt(
        source_hash=source_hash,
        exit_code=exit_code,
        root_cause_summary=summary,
        failing_locations=locations,
        verbatim_quotes=verbatim_quotes,
        producer="deterministic-extractor",
    )


def delegate_to_light_llm(
    raw_log: str,
    exit_code: int,
    model: str = "gemini-2.5-flash",
    custom_llm_fn: Optional[Callable[[str, int], Dict[str, Any]]] = None,
) -> Optional[DiagnosticReceipt]:
    """Optional light-LLM delegation for semantic receipt generation.

    Still subject to the exact same `verify_receipt()` deterministic gate!
    """
    source_hash = hashlib.sha256(raw_log.encode("utf-8")).hexdigest()[:12]
    if custom_llm_fn is not None:
        try:
            payload = custom_llm_fn(raw_log, exit_code)
            return DiagnosticReceipt(
                source_hash=payload.get("source_hash", source_hash),
                exit_code=payload.get("exit_code", exit_code),
                root_cause_summary=payload.get("root_cause_summary", ""),
                failing_locations=payload.get("failing_locations", []),
                verbatim_quotes=payload.get("verbatim_quotes", []),
                producer=f"llm-delegate:{model}",
            )
        except Exception:
            return None

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None

    prompt = (
        "You are an Evidence-Preserving Diagnostic Reducer (SoL-Pi arXiv:2609.20519). "
        "Given the raw terminal log below, return ONLY valid JSON with keys: "
        "`root_cause_summary` (1 concise sentence), `failing_locations` (array of 'file:line'), "
        "and `verbatim_quotes` (array of up to 15 exact, character-for-character substrings copied "
        "directly from the raw log without modifying a single space or character).\n\n"
        f"RAW LOG (Exit Code {exit_code}):\n{raw_log[:45000]}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req_body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=req_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            text_out = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text_out)
            return DiagnosticReceipt(
                source_hash=source_hash,
                exit_code=exit_code,
                root_cause_summary=str(parsed.get("root_cause_summary", "LLM diagnostic receipt")),
                failing_locations=list(parsed.get("failing_locations", [])),
                verbatim_quotes=list(parsed.get("verbatim_quotes", [])),
                producer=f"llm-delegate:{model}",
            )
    except Exception:
        return None


def format_receipt(receipt: DiagnosticReceipt, obs_id: Optional[str] = None) -> str:
    handle_tag = f" | Handle: {obs_id}" if obs_id else ""
    out = [
        f"[SoL-Pi Evidence-Preserving Receipt | Exit: {receipt.exit_code} | Hash: {receipt.source_hash}{handle_tag}]",
        f"Summary: {receipt.root_cause_summary}",
    ]
    if receipt.failing_locations:
        out.append("Locations: " + ", ".join(receipt.failing_locations))
    out.append("--- VERIFIED VERBATIM QUOTES (100% Exact Source Match) ---")
    for q in receipt.verbatim_quotes:
        out.append(f"  {q}")
    out.append("----------------------------------------------------------")
    return "\n".join(out)


def reduce_log_fail_open(
    raw_log: str,
    exit_code: int,
    *,
    obs_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    custom_llm_fn: Optional[Callable[[str, int], Dict[str, Any]]] = None,
) -> Tuple[str, bool, bool, int]:
    """Run Evidence-Preserving Reducer with Fail-Open fallback.

    Returns: `(output_text, was_reduced, failed_open, secrets_redacted_count)`
    """
    cfg = (config or load_config()).get("evidence_reducer", {})
    if not cfg.get("enabled", True):
        return raw_log, False, False, 0

    threshold = int(cfg.get("threshold_bytes", 4096))
    raw_bytes = len(raw_log.encode("utf-8"))

    secrets_count = 0
    working_log = raw_log
    if cfg.get("redact_secrets", True):
        working_log, secrets_count = scan_and_redact_secrets(raw_log)

    if raw_bytes < threshold:
        return working_log, False, False, secrets_count

    mode = cfg.get("mode", "hybrid")
    strict_check = bool(cfg.get("strict_verbatim_check", True))
    max_ratio = float(cfg.get("max_compression_ratio", 0.85))
    failed_open = False
    receipt: Optional[DiagnosticReceipt] = None

    # 1. Try LLM Delegate if configured or custom_llm_fn provided
    if mode in ("llm", "hybrid") and (custom_llm_fn is not None or os.environ.get("GEMINI_API_KEY")):
        candidate = delegate_to_light_llm(
            working_log,
            exit_code,
            model=cfg.get("delegate_model", "gemini-2.5-flash"),
            custom_llm_fn=custom_llm_fn,
        )
        if candidate is not None:
            v_res = verify_receipt(working_log, exit_code, candidate, strict_verbatim=strict_check)
            if v_res.valid:
                receipt = candidate
            else:
                failed_open = True
                if mode == "llm":
                    # Strict fail-open to raw working_log when in pure LLM mode
                    return working_log, False, True, secrets_count

    # 2. Deterministic extractor (if hybrid or deterministic mode)
    if receipt is None:
        candidate = build_deterministic_receipt(
            working_log,
            exit_code,
            max_quotes=int(cfg.get("max_verbatim_quotes", 35)),
            context_radius=int(cfg.get("context_radius_lines", 2)),
        )
        v_res = verify_receipt(working_log, exit_code, candidate, strict_verbatim=strict_check)
        if not v_res.valid:
            return working_log, False, True, secrets_count
        receipt = candidate

    formatted = format_receipt(receipt, obs_id=obs_id)
    if len(formatted.encode("utf-8")) >= int(raw_bytes * max_ratio):
        # Fail-Open: receipt isn't significantly smaller than raw log
        return working_log, False, True, secrets_count

    return formatted, True, failed_open, secrets_count
