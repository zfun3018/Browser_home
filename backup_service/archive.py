"""Create and validate the versioned full-data ZIP format."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import subprocess
import zipfile


FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = int(os.environ.get("BACKUP_MAX_ARCHIVE_BYTES", str(50 * 1024**3)))
MAX_EXPANDED_BYTES = int(os.environ.get("BACKUP_MAX_EXPANDED_BYTES", str(200 * 1024**3)))
MAX_FILES = int(os.environ.get("BACKUP_MAX_FILES", "200000"))


@dataclass(frozen=True)
class ArchiveSources:
    linkwarden_data: Path
    homepage_state: Path
    obsidian_note: Path
    compose_file: Path
    env_file: Path
    token_file: Path
    pg_dump: str = "pg_dump"


class ArchiveError(Exception):
    pass


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(source: Path, target: Path, prefix: str) -> list[Path]:
    if not source.is_dir():
        raise ArchiveError(f"Required backup directory is missing: {prefix}")
    result: list[Path] = []
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if item.is_symlink():
            raise ArchiveError(f"Symlinks are not supported in backup sources: {prefix}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise ArchiveError(f"Unsupported file in backup sources: {prefix}")
        copied = target / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, copied)
        result.append(copied)
    return result


def build_archive(job_dir: Path, sources: ArchiveSources, pg_env: dict[str, str]) -> Path:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = job_dir / "payload"
    payload.mkdir()
    files: list[tuple[str, Path]] = []

    try:
        dump_path = payload / "linkwarden" / "database.dump"
        dump_path.parent.mkdir(parents=True)
        result = subprocess.run(
            [sources.pg_dump, "--format=custom", "--no-owner", "--file", str(dump_path)],
            env={**os.environ, **pg_env}, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not dump_path.is_file():
            raise ArchiveError("PostgreSQL 导出失败")
        files.append(("linkwarden/database.dump", dump_path))

        data_files = _copy_tree(sources.linkwarden_data, payload / "linkwarden" / "data", "Linkwarden attachments")
        files.extend((f"linkwarden/data/{item.relative_to(payload / 'linkwarden' / 'data').as_posix()}", item) for item in data_files)

        state_source = Path(sources.homepage_state) / "state.db"
        if not state_source.is_file():
            raise ArchiveError("首页状态数据库不存在")
        state_target = payload / "homepage" / "state.db"
        state_target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(state_source) as source, sqlite3.connect(state_target) as target:
            source.backup(target)
        files.append(("homepage/state.db", state_target))

        conflict_files = _copy_tree(Path(sources.homepage_state) / "note-conflicts", payload / "homepage" / "note-conflicts", "note conflict copies")
        files.extend((f"homepage/note-conflicts/{item.relative_to(payload / 'homepage' / 'note-conflicts').as_posix()}", item) for item in conflict_files)

        fixed_sources = (
            ("homepage/浏览器首页笔记.md", sources.obsidian_note),
            ("deployment/docker-compose.yml", sources.compose_file),
            ("deployment/.env", sources.env_file),
            ("deployment/homepage-secrets/linkwarden_api_token.txt", sources.token_file),
        )
        for archive_name, source in fixed_sources:
            source = Path(source)
            if not source.is_file() or source.is_symlink():
                raise ArchiveError(f"Required backup file is missing: {archive_name}")
            target = payload.joinpath(*PurePosixPath(archive_name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files.append((archive_name, target))

        files.sort(key=lambda row: row[0])
        manifest = {
            "formatVersion": FORMAT_VERSION,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "applicationVersion": "1",
            "files": [{"path": name, "size": path.stat().st_size, "sha256": _hash(path)} for name, path in files],
        }
        (payload / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (payload / "RESTORE.txt").write_text(
            "浏览器首页与 Linkwarden 备份\n\n"
            "此 ZIP 包含 PostgreSQL 数据库、Linkwarden 附件、首页状态、Obsidian 笔记、部署配置和明文凭据。"
            "请像保管密码文件一样保护本文件。\n\n"
            "在另一台电脑恢复时，先安全恢复 deployment/.env 和 deployment/homepage-secrets/linkwarden_api_token.txt，"
            "再使用首页的导入功能恢复数据。导入不会覆盖当前电脑的部署配置。\n",
            encoding="utf-8",
        )

        archive = job_dir / "backup.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as output:
            output.writestr("linkwarden/data/", "")
            output.writestr("homepage/note-conflicts/", "")
            output.write(payload / "RESTORE.txt", "RESTORE.txt")
            for name, source in files:
                output.write(source, name)
            output.write(payload / "manifest.json", "manifest.json")
        return archive
    except ArchiveError:
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        raise ArchiveError("备份过程中读取或写入文件失败") from exc


def validate_archive(archive: Path, extract_to: Path, pg_restore: str = "pg_restore") -> dict:
    archive = Path(archive)
    extract_to = Path(extract_to)
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveError("备份文件超过允许大小或无法读取")
    created = False
    try:
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if len(entries) > MAX_FILES:
                raise ArchiveError("备份文件包含过多项目")
            seen: set[str] = set()
            expanded_size = 0
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                mode = entry.external_attr >> 16
                if (not name or "\\" in name or path.is_absolute() or ".." in path.parts
                        or name.startswith("/") or name.startswith("//") or ":" in path.parts[0]
                        or path.as_posix() != name.rstrip("/") or stat.S_ISLNK(mode) or name in seen):
                    raise ArchiveError("备份文件包含不安全路径")
                allowed = (
                    name in {"manifest.json", "RESTORE.txt", "linkwarden/database.dump", "linkwarden/data/",
                             "homepage/state.db", "homepage/浏览器首页笔记.md", "homepage/note-conflicts/",
                             "deployment/docker-compose.yml", "deployment/.env",
                             "deployment/homepage-secrets/linkwarden_api_token.txt"}
                    or name.startswith("linkwarden/data/")
                    or name.startswith("homepage/note-conflicts/")
                )
                if not allowed:
                    raise ArchiveError("备份文件包含不支持的项目")
                if name.startswith("linkwarden/data/") and len(path.parts) > 2 and path.parts[2].startswith(".restore-"):
                    raise ArchiveError("备份附件目录包含保留的恢复路径")
                seen.add(name)
                expanded_size += entry.file_size
                if expanded_size > MAX_EXPANDED_BYTES:
                    raise ArchiveError("备份解压后超过允许大小")
            if "manifest.json" not in seen or "linkwarden/database.dump" not in seen:
                raise ArchiveError("备份文件缺少必要内容")
            extract_to.mkdir(parents=True, exist_ok=False)
            created = True
            for entry in entries:
                name = entry.filename
                if name.endswith("/"):
                    (extract_to / Path(*PurePosixPath(name).parts)).mkdir(parents=True, exist_ok=True)
                    continue
                target = extract_to.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as input_file, target.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            manifest = json.loads((extract_to / "manifest.json").read_text(encoding="utf-8"))
    except ArchiveError:
        if created:
            shutil.rmtree(extract_to, ignore_errors=True)
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if created:
            shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("备份文件损坏或格式无效") from exc

    if (not isinstance(manifest, dict) or manifest.get("formatVersion") != FORMAT_VERSION
            or not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("createdAt"), str)):
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("不支持此备份格式")
    manifest_files = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] in manifest_files:
            shutil.rmtree(extract_to, ignore_errors=True)
            raise ArchiveError("备份清单无效")
        manifest_files[item["path"]] = item
    archive_files = {name for name in seen if name not in {"manifest.json", "RESTORE.txt"} and not name.endswith("/")}
    if set(manifest_files) != archive_files:
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("备份清单与文件内容不一致")
    for name, item in manifest_files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or not name:
            shutil.rmtree(extract_to, ignore_errors=True)
            raise ArchiveError("备份清单包含不安全路径")
        file_path = extract_to.joinpath(*PurePosixPath(name).parts)
        if (not file_path.is_file() or not isinstance(item.get("size"), int)
                or file_path.stat().st_size != item.get("size") or not isinstance(item.get("sha256"), str)
                or _hash(file_path) != item.get("sha256")):
            shutil.rmtree(extract_to, ignore_errors=True)
            raise ArchiveError("备份文件校验失败")

    required = {
        "linkwarden/database.dump", "homepage/state.db", "homepage/浏览器首页笔记.md",
        "deployment/docker-compose.yml", "deployment/.env", "deployment/homepage-secrets/linkwarden_api_token.txt",
    }
    if not required.issubset(manifest_files) or "RESTORE.txt" not in seen:
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("备份文件缺少必要内容")
    try:
        dump_check = subprocess.run([pg_restore, "--list", str(extract_to / "linkwarden" / "database.dump")], capture_output=True, check=False)
        if dump_check.returncode != 0:
            raise ArchiveError("PostgreSQL 备份无法读取")
        with sqlite3.connect(extract_to / "homepage" / "state.db") as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ArchiveError("首页状态数据库损坏")
    except (OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("无法验证备份数据库") from exc
    return manifest
