from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ETF_REPORT_RE = re.compile(r"^etf50_(\d{8})_(\d{4})\.json$")
SECRET_VALUE_RE = re.compile(
    r"(?i)(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,}|bearer\s+[A-Za-z0-9._-]{16,}"
)
SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}
REQUIRED_REPORT_KEYS = {
    "datetime",
    "total",
    "bullish",
    "neutral",
    "bearish",
    "top3",
    "results",
}
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_REPORT_AGE = timedelta(hours=6)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class CloudSyncError(RuntimeError):
    pass


class SyncAlreadyRunningError(CloudSyncError):
    pass


class DirtyWorktreeError(CloudSyncError):
    pass


class ArtifactValidationError(CloudSyncError):
    pass


@dataclass(frozen=True)
class PublishArtifact:
    source_path: Path
    relative_path: Path
    generated_at: datetime


@dataclass(frozen=True)
class PublishResult:
    status: str
    artifact: str
    commit: str | None = None


@contextmanager
def exclusive_sync_lock(lock_path: Path) -> Iterator[None]:
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)

    try:
        _lock_file(handle)
    except OSError as exc:
        handle.close()
        raise SyncAlreadyRunningError("another scan-and-sync process is already running") from exc

    try:
        yield
    finally:
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()


def publish_latest_etf_report(
    root: Path,
    *,
    reports_dir: Path | None = None,
    remote: str = "origin",
    branch: str = "deploy",
    lock_path: Path | None = None,
    now: datetime | None = None,
) -> PublishResult:
    root = Path(root).resolve()
    reports_dir = Path(reports_dir or root / "reports").resolve()
    now = now or datetime.now()
    lock_path = lock_path or _default_lock_path(root)

    with exclusive_sync_lock(lock_path):
        _ensure_clean_worktree(root)
        artifact = find_latest_publishable_report(reports_dir, now=now)
        remote_url = _git(root, "remote", "get-url", remote).stdout.strip()
        if not remote_url:
            raise CloudSyncError(f"git remote {remote!r} has no URL")

        with tempfile.TemporaryDirectory(prefix="nasdx-cloud-sync-") as temp_dir:
            clone_dir = Path(temp_dir) / "publish"
            _git(
                root,
                "clone",
                "--branch",
                branch,
                "--single-branch",
                "--depth",
                "1",
                "--no-tags",
                remote_url,
                str(clone_dir),
            )
            _git(clone_dir, "config", "user.name", "NASDX Automation")
            _git(clone_dir, "config", "user.email", "nasdx-automation@localhost")

            destination = clone_dir / artifact.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact.source_path, destination)
            relative = artifact.relative_path.as_posix()
            _git(clone_dir, "add", "-f", "--", relative)

            diff = _git(clone_dir, "diff", "--cached", "--quiet", check=False)
            if diff.returncode == 0:
                return PublishResult(status="no_changes", artifact=relative)
            if diff.returncode != 1:
                raise CloudSyncError("git diff failed while checking the publish artifact")

            message = f"data: publish ETF50 scan {artifact.generated_at:%Y-%m-%d %H:%M}"
            _git(clone_dir, "commit", "-m", message)
            commit = _git(clone_dir, "rev-parse", "HEAD").stdout.strip()
            _git(clone_dir, "push", "origin", f"HEAD:{branch}")
            return PublishResult(status="published", artifact=relative, commit=commit)


def find_latest_publishable_report(reports_dir: Path, *, now: datetime) -> PublishArtifact:
    candidates = []
    for path in Path(reports_dir).glob("etf50_*.json"):
        match = ETF_REPORT_RE.fullmatch(path.name)
        if match:
            stamp = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M")
            candidates.append((stamp, path))
    if not candidates:
        raise ArtifactValidationError("no whitelisted ETF50 JSON report was found")

    _stamp, source_path = max(candidates, key=lambda item: item[0])
    payload, generated_at = validate_publishable_report(source_path, now=now)
    del payload
    return PublishArtifact(
        source_path=source_path,
        relative_path=Path("reports") / source_path.name,
        generated_at=generated_at,
    )


def validate_publishable_report(path: Path, *, now: datetime) -> tuple[dict[str, Any], datetime]:
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ArtifactValidationError(f"report exceeds {MAX_REPORT_BYTES} bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError("report is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactValidationError("report root must be a JSON object")

    missing = sorted(REQUIRED_REPORT_KEYS - set(payload))
    if missing:
        raise ArtifactValidationError("report schema missing: " + ", ".join(missing))
    results = payload.get("results")
    total = payload.get("total")
    if not isinstance(results, list) or not isinstance(total, int) or total != len(results):
        raise ArtifactValidationError("report total must match the results list")
    if not isinstance(payload.get("top3"), list):
        raise ArtifactValidationError("report top3 must be a list")

    generated_at = _parse_datetime(payload.get("datetime"))
    comparable_now, comparable_generated = _comparable_datetimes(now, generated_at)
    age = comparable_now - comparable_generated
    if age > MAX_REPORT_AGE or age < -MAX_FUTURE_SKEW:
        raise ArtifactValidationError("report generation time is outside the allowed publish window")

    match = ETF_REPORT_RE.fullmatch(path.name)
    expected_stamp = comparable_generated.strftime("%Y%m%d_%H%M")
    if not match or path.name != f"etf50_{expected_stamp}.json":
        raise ArtifactValidationError("report filename does not match its generation time")
    _reject_sensitive_values(payload)
    return payload, generated_at


def _ensure_clean_worktree(root: Path) -> None:
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if status.stdout.strip():
        raise DirtyWorktreeError("source worktree has tracked or staged changes; publish aborted")


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        detail = _redact((proc.stderr or proc.stdout or "").strip())
        suffix = f": {detail[-300:]}" if detail else ""
        action = args[0] if args else "command"
        raise CloudSyncError(f"git {action} failed with exit code {proc.returncode}{suffix}")
    return proc


def _reject_sensitive_values(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in SENSITIVE_KEYS or normalized.endswith(("apikey", "password", "secret", "token")):
                raise ArtifactValidationError(f"sensitive field is not publishable: {path}.{key}")
            _reject_sensitive_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_values(child, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        raise ArtifactValidationError(f"sensitive value is not publishable: {path}")


def _parse_datetime(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactValidationError("report datetime is invalid") from exc


def _comparable_datetimes(now: datetime, generated_at: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None and generated_at.tzinfo is None:
        return now, generated_at
    normalized_now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    normalized_generated = (
        generated_at.replace(tzinfo=timezone.utc)
        if generated_at.tzinfo is None
        else generated_at.astimezone(timezone.utc)
    )
    return normalized_now, normalized_generated


def _default_lock_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).lower().encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"nasdx-scan-and-sync-{digest}.lock"


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _redact(text: str) -> str:
    text = SECRET_VALUE_RE.sub("<redacted>", text)
    return re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1<redacted>@", text)
