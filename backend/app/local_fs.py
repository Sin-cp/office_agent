"""Local filesystem access for the single-user desktop use case.

Paths are only accepted inside explicitly allowed roots (the current user's
home directory by default). Reading never trusts model output: the task layer
passes the path the model extracted from the user message, and every call
re-validates the resolved path. Overwriting an original file is a separate,
explicit operation that requires the stored identity of the file to still match.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import database
from .storage import MAX_BYTES, validate_docx

MAX_SCAN = 2000
MAX_LISTED_NAMES = 50
MAX_EXTENSION_GROUPS = 12
DOCX_SUFFIX = ".docx"


class LocalError(Exception):
    """A user-facing local filesystem failure with an HTTP-like status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _enabled(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def read_enabled() -> bool:
    return _enabled("OFFICE_AGENT_LOCAL_FS")


def write_enabled() -> bool:
    return _enabled("OFFICE_AGENT_LOCAL_WRITE")


def allowed_roots() -> list[Path]:
    roots = [Path.home().resolve()]
    for part in os.getenv("OFFICE_AGENT_LOCAL_ROOTS", "").split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return roots


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _clean_path(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def resolve_path(raw: str, kind: str | None = None) -> Path:
    """Resolve one explicit path and enforce the configured roots."""
    if not read_enabled():
        raise LocalError(403, "本地文件功能已关闭（OFFICE_AGENT_LOCAL_FS=1 开启）")
    text = _clean_path(raw)
    if not text:
        raise LocalError(400, "请提供本地文件的绝对路径")
    if "\x00" in text:
        raise LocalError(400, "路径包含无效字符")
    try:
        candidate = Path(text).expanduser()
    except RuntimeError as exc:
        raise LocalError(400, "无法解析用户主目录，请使用绝对路径") from exc
    if not candidate.is_absolute():
        raise LocalError(400, "请输入绝对路径（可使用 ~/...）")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalError(404, "文件或目录不存在，或无法访问") from exc
    if not _inside(resolved, allowed_roots()):
        raise LocalError(403, "仅支持当前用户主目录（或已配置目录）内的文件")
    if kind == "file" and not resolved.is_file():
        raise LocalError(400, "该路径不是文件")
    if kind == "dir" and not resolved.is_dir():
        raise LocalError(400, "该路径不是目录")
    return resolved


def valid_docx_name(name: str) -> bool:
    return (
        bool(name)
        and name.lower().endswith(DOCX_SUFFIX)
        and len(name) <= 255
        and not any(ord(character) < 32 for character in name)
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> dict:
    try:
        stat = path.stat()
        return dict(sha256=sha256(path), dev=stat.st_dev, ino=stat.st_ino)
    except OSError as exc:
        raise LocalError(404, "文件不存在或无法访问") from exc


def _scan(path: Path, recursive: bool):
    files: list[Path] = []
    directories: list[Path] = []
    truncated = False
    if not recursive:
        try:
            for child in path.iterdir():
                if len(files) + len(directories) >= MAX_SCAN:
                    truncated = True
                    break
                try:
                    if child.is_dir():
                        directories.append(child)
                    elif child.is_file():
                        files.append(child)
                except OSError:
                    continue
        except OSError as exc:
            raise LocalError(403, "无法读取该目录") from exc
        return files, directories, truncated
    try:
        for root, dirnames, filenames in os.walk(path, followlinks=False):
            for name in dirnames:
                if len(files) + len(directories) >= MAX_SCAN:
                    truncated = True
                    break
                directories.append(Path(root) / name)
            if truncated:
                break
            for name in filenames:
                if len(files) + len(directories) >= MAX_SCAN:
                    truncated = True
                    break
                files.append(Path(root) / name)
            if truncated:
                break
    except OSError as exc:
        raise LocalError(403, "无法读取该目录") from exc
    return files, directories, truncated


def list_directory(path: Path, recursive: bool = False) -> dict:
    """Read-only metadata: never reads file contents."""
    files, directories, truncated = _scan(path, recursive)
    counts: dict[str, int] = {}
    for item in files:
        suffix = item.suffix.lower() or "(无后缀)"
        counts[suffix] = counts.get(suffix, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    groups = dict(ranked[:MAX_EXTENSION_GROUPS])
    remaining = sum(value for _, value in ranked[MAX_EXTENSION_GROUPS:])
    if remaining:
        groups["(其他)"] = remaining
    names: list[str] = []
    for item in files[:MAX_LISTED_NAMES]:
        try:
            names.append(str(item.relative_to(path)) if recursive else item.name)
        except ValueError:
            names.append(item.name)
    summary = f"目录 {path} 下共 {len(files)} 个文件、{len(directories)} 个子目录"
    if groups:
        detail = "、".join(f"{key} {value} 个" for key, value in groups.items())
        summary += f"；{detail}"
    summary += "。"
    if truncated:
        summary += f"（条目过多，只统计了前 {MAX_SCAN} 项）"
    return dict(
        path=str(path),
        files=len(files),
        dirs=len(directories),
        by_extension=groups,
        names=names,
        truncated=truncated,
        summary_text=summary,
    )


def collect_docx(
    path: Path, recursive: bool = False, limit: int = 20
) -> tuple[list[Path], int]:
    """Return the .docx candidates (capped) and how many were found in total."""
    if path.is_file():
        if path.suffix.lower() != DOCX_SUFFIX:
            suffix = path.suffix or "无后缀"
            raise LocalError(400, f"仅支持 .docx 文件，当前是 {suffix}")
        return [path], 1
    files, _, _ = _scan(path, recursive)
    candidates = [item for item in files if item.suffix.lower() == DOCX_SUFFIX]
    candidates.sort(key=lambda item: item.name)
    return candidates[:limit], len(candidates)


def import_docx(root: Path, project_id: str, source: Path) -> dict:
    """Validate a local DOCX and copy it into the project's private storage."""
    if not valid_docx_name(source.name):
        raise LocalError(400, "仅支持文件名合法的 .docx 文件")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise LocalError(404, "文件不存在或无法访问") from exc
    if size == 0:
        raise LocalError(400, "不能导入空文件")
    if size > MAX_BYTES:
        raise LocalError(413, "文件不能超过 10 MiB")
    identity = str(uuid4())
    key = f"{identity}.docx"
    temporary = root / "uploads" / f"{identity}.tmp"
    destination = root / "uploads" / key
    copied = 0
    committed = False
    try:
        with source.open("rb") as stream, temporary.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_BYTES:
                    raise LocalError(413, "文件不能超过 10 MiB")
                output.write(chunk)
        if copied == 0:
            raise LocalError(400, "不能导入空文件")
        try:
            validate_docx(temporary)
        except ValueError as exc:
            raise LocalError(400, str(exc)) from exc
        record = dict(
            id=identity,
            name=source.name,
            size=copied,
            storage_key=key,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        temporary.replace(destination)
        try:
            database.insert(root, {**record, "project_id": project_id})
        except (OSError, sqlite3.Error) as exc:
            raise LocalError(500, "文件保存失败，请稍后重试") from exc
        committed = True
        return record
    except OSError as exc:
        raise LocalError(500, "文件保存失败，请稍后重试") from exc
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            destination.unlink(missing_ok=True)


def write_in_place(path: Path, data: bytes, expected: dict) -> Path:
    """Overwrite one original file after re-checking it is still the same bytes.

    The caller must have obtained explicit user confirmation first. A timestamped
    backup is written next to the original before the atomic replacement.
    """
    if not write_enabled():
        raise LocalError(
            403, "覆盖源文件功能已关闭（OFFICE_AGENT_LOCAL_WRITE=1 开启）"
        )
    resolved = resolve_path(str(path), "file")
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise LocalError(404, "源文件不存在，无法覆盖") from exc
    if (stat.st_dev, stat.st_ino) != (expected.get("dev"), expected.get("ino")):
        raise LocalError(409, "源文件已被替换，未覆盖")
    try:
        if sha256(resolved) != expected.get("sha256"):
            raise LocalError(409, "源文件在读取后已被修改，未覆盖")
    except OSError as exc:
        raise LocalError(404, "源文件不存在，无法覆盖") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = resolved.with_name(f"{resolved.name}.bak-{stamp}")
    counter = 1
    while backup.exists():
        backup = resolved.with_name(f"{resolved.name}.bak-{stamp}-{counter}")
        counter += 1
    temporary = resolved.with_name(f".{resolved.name}.tmp-{uuid4().hex}")
    try:
        shutil.copy2(resolved, backup)
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, resolved)
        os.chmod(resolved, stat.st_mode)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LocalError(500, "写入原文件失败，未覆盖") from exc
    return backup


def restore(backup: Path, path: Path) -> None:
    """Put the backup bytes back after a failed publication, keeping the backup."""
    data = backup.read_bytes()
    temporary = path.with_name(f".{path.name}.restore-{uuid4().hex}")
    with temporary.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
