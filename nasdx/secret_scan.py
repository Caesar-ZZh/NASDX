"""Multi-provider secret scanning for NASDX (working tree + full git history).

This module replaces the previous single ``sk-[A-Za-z0-9_-]{20,}`` working-tree
regex that guarded the repository.  That guard had three blind spots:

1. it only recognised OpenAI-style keys, so GitHub PATs, AWS keys, private
   keys, Slack tokens and generic high-entropy credentials passed straight
   through;
2. it only looked at the current tree, so "commit a key, delete it in the next
   commit" was never blocked;
3. it printed the first eight characters of every match, which leaks the
   credential prefix into CI logs.

The scanner here fixes all three:

* :data:`RULES` is a rule table of credential shapes with per-rule entropy
  gates and placeholder filtering;
* :func:`scan_history` walks every blob reachable from every ref, so a secret
  that was added and later deleted still fails the gate;
* :class:`Finding` only ever renders ``rule / path / line / fingerprint``.  The
  fingerprint is a truncated SHA-256 of the matched secret, which is stable
  enough to allowlist but useless to an attacker reading CI logs.

Allowlisting is loaded from ``security/secret_scan_allowlist.toml`` and is
validated to stay narrow: every entry needs a human reason plus a path glob or
a fingerprint, so "disable the whole rule" cannot be smuggled in.
"""
from __future__ import annotations

import fnmatch
import hashlib
import math
import re
import shutil
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_RELPATH = "security/secret_scan_allowlist.toml"

#: Text-like suffixes that are worth scanning.  Binary blobs are skipped even
#: when the suffix matches, see :func:`_looks_binary`.
SOURCE_SUFFIXES = (
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".ps1",
    ".bat",
    ".cmd",
    ".sh",
    ".txt",
    ".json",
    ".cfg",
    ".ini",
    ".conf",
    ".env",
    ".iss",
)

FALLBACK_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "wheelhouse",
        "reports",
        "desktop_logs",
        "htmlcov",
    }
)

MAX_BLOB_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class SecretRule:
    """A single credential shape the gate refuses to accept."""

    rule_id: str
    description: str
    pattern: re.Pattern[str]
    secret_group: int = 0
    entropy_min: float = 0.0
    reject_placeholders: bool = False


@dataclass(frozen=True)
class Finding:
    """A redacted secret hit.  Never carries the secret itself."""

    rule_id: str
    path: str
    line: int
    fingerprint: str
    source: str = "worktree"
    blob: str = ""

    def redacted(self) -> str:
        parts = [
            f"rule={self.rule_id}",
            f"path={self.path}",
            f"line={self.line}",
            f"fingerprint={self.fingerprint}",
        ]
        if self.blob:
            parts.append(f"blob={self.blob[:12]}")
        return " ".join(parts)

    def __str__(self) -> str:  # pragma: no cover - trivial delegation
        return self.redacted()


class AllowlistError(ValueError):
    """Raised when the committed allowlist is malformed or too broad."""


@dataclass(frozen=True)
class AllowEntry:
    reason: str
    rule: str | None = None
    path: str | None = None
    fingerprint: str | None = None

    def matches(self, finding: Finding) -> bool:
        if self.rule is not None and self.rule != finding.rule_id:
            return False
        if self.path is not None and not fnmatch.fnmatch(finding.path, self.path):
            return False
        if self.fingerprint is not None and self.fingerprint != finding.fingerprint:
            return False
        return True


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

# Patterns are written so they cannot match their own source line in this file
# (every literal prefix is immediately followed by a character class, never by
# a run of characters that satisfies the class).

RULES: tuple[SecretRule, ...] = (
    SecretRule(
        rule_id="openai-style-api-key",
        description="OpenAI / DeepSeek / Anthropic / OpenRouter style sk-* key",
        pattern=re.compile(r"\bsk-(?:proj-|ant-|or-v1-|live-)?[A-Za-z0-9_-]{20,}\b"),
        entropy_min=2.6,
        reject_placeholders=True,
    ),
    SecretRule(
        rule_id="github-token",
        description="GitHub personal access / OAuth / app token",
        pattern=re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="github-fine-grained-pat",
        description="GitHub fine-grained personal access token",
        pattern=re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="aws-access-key-id",
        description="AWS access key id",
        pattern=re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|AIDA|AROA)[0-9A-Z]{16}\b"),
        entropy_min=2.5,
    ),
    SecretRule(
        rule_id="aws-secret-access-key",
        description="AWS secret access key assigned to an aws-ish name",
        pattern=re.compile(
            r"(?i)aws[_-]?(?:secret|sec)[_-]?(?:access[_-]?)?key\s*[:=]\s*[\"']?"
            r"([A-Za-z0-9/+=]{40})[\"']?"
        ),
        secret_group=1,
        entropy_min=3.5,
        reject_placeholders=True,
    ),
    SecretRule(
        rule_id="private-key-block",
        description="PEM / OpenSSH / PGP private key block",
        pattern=re.compile(r"-----BEGIN [A-Z0-9 ]{0,20}PRIVATE KEY[A-Z ]{0,10}-----"),
    ),
    SecretRule(
        rule_id="slack-token",
        description="Slack bot / user / app token",
        pattern=re.compile(r"\bxox[baprse]-[A-Za-z0-9-]{10,}\b"),
        entropy_min=2.5,
    ),
    SecretRule(
        rule_id="slack-webhook",
        description="Slack incoming webhook URL",
        pattern=re.compile(
            r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]{16,}"
        ),
    ),
    SecretRule(
        rule_id="google-api-key",
        description="Google / Firebase API key",
        pattern=re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="stripe-secret-key",
        description="Stripe live / restricted secret key",
        pattern=re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="pypi-token",
        description="PyPI upload token",
        pattern=re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}\b"),
        entropy_min=3.5,
    ),
    SecretRule(
        rule_id="npm-token",
        description="npm automation token",
        pattern=re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="telegram-bot-token",
        description="Telegram bot token",
        pattern=re.compile(r"\b\d{8,12}:AA[A-Za-z0-9_-]{32,}\b"),
        entropy_min=3.0,
    ),
    SecretRule(
        rule_id="jwt",
        description="Signed JSON Web Token",
        pattern=re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{16,}"),
        entropy_min=3.5,
    ),
    SecretRule(
        rule_id="generic-assigned-secret",
        description="High-entropy literal assigned to a credential-looking name",
        pattern=re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|bearer[_-]?token"
            r"|client[_-]?secret|secret[_-]?key|private[_-]?token|refresh[_-]?token"
            r"|password|passwd)\b\s*[:=]\s*[\"']([^\"'\s]{16,200})[\"']"
        ),
        secret_group=1,
        entropy_min=3.6,
        reject_placeholders=True,
    ),
)

RULE_IDS = frozenset(rule.rule_id for rule in RULES)

#: Substrings that mark a literal as documentation rather than a credential.
PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "your",
    "yours",
    "dummy",
    "sample",
    "changeme",
    "change-me",
    "change_me",
    "redacted",
    "todo",
    "fixme",
    "xxxx",
    "abcdefgh",
    "0123456789",
    "os.environ",
    "getenv",
    "${",
    "{{",
    "<",
    ">",
    "******",
    "......",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fingerprint(secret: str) -> str:
    """Stable, non-reversible identifier for a matched secret."""
    return hashlib.sha256(secret.encode("utf-8", errors="replace")).hexdigest()[:16]


def scan_text(
    text: str,
    path: str,
    *,
    source: str = "worktree",
    blob: str = "",
    rules: Sequence[SecretRule] = RULES,
) -> list[Finding]:
    """Return redacted findings for one text buffer."""
    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 8000:
            line = line[:8000]
        for rule in rules:
            for match in rule.pattern.finditer(line):
                secret = match.group(rule.secret_group) or ""
                if not secret:
                    continue
                if rule.reject_placeholders and is_placeholder(secret):
                    continue
                if rule.entropy_min and shannon_entropy(secret) < rule.entropy_min:
                    continue
                digest = fingerprint(secret)
                key = (rule.rule_id, lineno, digest)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        path=path,
                        line=lineno,
                        fingerprint=digest,
                        source=source,
                        blob=blob,
                    )
                )
    return findings


def scan_worktree(
    root: Path = ROOT,
    *,
    allowlist: Sequence[AllowEntry] | None = None,
) -> tuple[list[Finding], int]:
    """Scan versionable text files in the working tree.

    Returns ``(findings, files_scanned)``.  Findings are already filtered
    through the allowlist.
    """
    root = Path(root)
    entries = load_allowlist(root) if allowlist is None else list(allowlist)
    findings: list[Finding] = []
    files = iter_candidate_files(root)
    for path in files:
        rel = _rel(path, root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                Finding(
                    rule_id="unreadable-file",
                    path=rel,
                    line=0,
                    fingerprint=fingerprint(f"{rel}:{exc.__class__.__name__}"),
                )
            )
            continue
        findings.extend(scan_text(text, rel, source="worktree"))
    return apply_allowlist(findings, entries), len(files)


def scan_history(
    root: Path = ROOT,
    *,
    allowlist: Sequence[AllowEntry] | None = None,
) -> tuple[list[Finding], int]:
    """Scan every text blob reachable from any ref.

    This is what catches "committed a key, deleted it in the next commit".
    Blobs are de-duplicated by object id, so the cost is proportional to the
    number of distinct file versions rather than to ``commits x files``.
    Returns ``(findings, blobs_scanned)``.
    """
    root = Path(root)
    entries = load_allowlist(root) if allowlist is None else list(allowlist)
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git executable not found; cannot run history scan")

    listing = subprocess.run(
        [git, "rev-list", "--objects", "--all"],
        cwd=str(root),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if listing.returncode != 0:
        raise RuntimeError(f"git rev-list failed: {listing.stderr.strip()[:200]}")

    wanted: dict[str, str] = {}
    for raw in listing.stdout.splitlines():
        oid, _, rel = raw.partition(" ")
        rel = rel.strip()
        if not rel or len(oid) < 7:
            continue
        if not _has_source_suffix(rel):
            continue
        wanted.setdefault(oid, rel)

    if not wanted:
        return [], 0

    batch = subprocess.run(
        [git, "cat-file", "--batch"],
        cwd=str(root),
        input="\n".join(wanted).encode("ascii"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if batch.returncode != 0:
        raise RuntimeError("git cat-file --batch failed")

    findings: list[Finding] = []
    scanned = 0
    for oid, otype, payload in _iter_batch(batch.stdout):
        if otype != "blob":
            continue
        scanned += 1
        if len(payload) > MAX_BLOB_BYTES or _looks_binary(payload):
            continue
        text = payload.decode("utf-8", errors="replace")
        findings.extend(
            scan_text(text, wanted.get(oid, oid), source="history", blob=oid)
        )
    return apply_allowlist(findings, entries), scanned


def load_allowlist(root: Path = ROOT) -> list[AllowEntry]:
    """Load and validate ``security/secret_scan_allowlist.toml``.

    A missing file is fine (empty allowlist).  A malformed or overly broad file
    is an error: silently ignoring a bad allowlist would silently weaken the
    gate.
    """
    path = Path(root) / ALLOWLIST_RELPATH
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AllowlistError(f"{ALLOWLIST_RELPATH}: cannot parse: {exc}") from exc

    raw_entries = data.get("allow", [])
    if not isinstance(raw_entries, list):
        raise AllowlistError(f"{ALLOWLIST_RELPATH}: 'allow' must be an array of tables")

    entries: list[AllowEntry] = []
    for index, raw in enumerate(raw_entries):
        label = f"{ALLOWLIST_RELPATH}[allow][{index}]"
        if not isinstance(raw, dict):
            raise AllowlistError(f"{label}: must be a table")
        unknown = set(raw) - {"reason", "rule", "path", "fingerprint"}
        if unknown:
            raise AllowlistError(f"{label}: unknown keys {sorted(unknown)}")

        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise AllowlistError(f"{label}: 'reason' is required and must be non-empty")

        rule = raw.get("rule")
        if rule is not None:
            if not isinstance(rule, str) or rule not in RULE_IDS:
                raise AllowlistError(f"{label}: unknown rule {rule!r}")

        path_glob = raw.get("path")
        if path_glob is not None:
            if not isinstance(path_glob, str) or not path_glob.strip():
                raise AllowlistError(f"{label}: 'path' must be a non-empty glob")
            if path_glob.strip() in {"*", "**", "**/*", "*/*", ".", "./*"}:
                raise AllowlistError(
                    f"{label}: path glob {path_glob!r} is too broad; scope it to a file or directory"
                )

        digest = raw.get("fingerprint")
        if digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{8,64}", digest):
                raise AllowlistError(f"{label}: 'fingerprint' must be a lowercase hex digest")

        if path_glob is None and digest is None:
            raise AllowlistError(
                f"{label}: needs 'path' or 'fingerprint'; a bare rule exemption disables the rule everywhere"
            )

        entries.append(
            AllowEntry(reason=reason.strip(), rule=rule, path=path_glob, fingerprint=digest)
        )
    return entries


def apply_allowlist(
    findings: Iterable[Finding], entries: Sequence[AllowEntry]
) -> list[Finding]:
    if not entries:
        return list(findings)
    return [f for f in findings if not any(entry.matches(f) for entry in entries)]


def iter_candidate_files(root: Path = ROOT) -> list[Path]:
    """Versionable text-like files, excluding generated output."""
    root = Path(root)
    git_files = _git_candidate_files(root)
    if git_files is not None:
        return git_files

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in FALLBACK_IGNORE_DIRS for part in path.relative_to(root).parts):
            continue
        if _has_source_suffix(path.name):
            files.append(path)
    return sorted(files)


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def is_placeholder(value: str) -> bool:
    """True when a literal is clearly documentation, not a live credential."""
    lowered = value.lower()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True
    body = re.sub(r"^(?:sk|rk|pk)[-_](?:proj|ant|or-v1|live|test)?[-_]?", "", lowered)
    if len(set(body)) <= 2:
        return True
    if body.isdigit():
        return True
    return False


def format_findings(findings: Sequence[Finding], limit: int = 5) -> str:
    if not findings:
        return ""
    shown = "; ".join(f.redacted() for f in findings[:limit])
    if len(findings) > limit:
        shown += f"; (+{len(findings) - limit} more)"
    return shown


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_batch(payload: bytes):
    pos = 0
    size = len(payload)
    while pos < size:
        nl = payload.find(b"\n", pos)
        if nl == -1:
            return
        header = payload[pos:nl].decode("utf-8", errors="replace").split()
        if len(header) < 3 or header[1] == "missing":
            pos = nl + 1
            continue
        oid, otype = header[0], header[1]
        try:
            length = int(header[2])
        except ValueError:
            pos = nl + 1
            continue
        start = nl + 1
        end = start + length
        yield oid, otype, payload[start:end]
        pos = end + 1


def _git_candidate_files(root: Path) -> list[Path] | None:
    git = shutil.which("git")
    if not git:
        return None
    proc = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=str(root),
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    files: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        path = root / rel
        if path.is_file() and _has_source_suffix(rel):
            files.append(path)
    return sorted(files)


def _has_source_suffix(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in SOURCE_SUFFIXES)


def _looks_binary(payload: bytes) -> bool:
    return b"\x00" in payload[:4096]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
