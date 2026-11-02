"""Create and validate versioned archives for the local homepage runtime."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import zipfile

FORMAT_VERSION = 2
MAX_ARCHIVE_BYTES = int(os.environ.get("BACKUP_MAX_ARCHIVE_BYTES", str(50 * 1024**3)))
MAX_EXPANDED_BYTES = int(os.environ.get("BACKUP_MAX_EXPANDED_BYTES", str(200 * 1024**3)))
MAX_FILES = int(os.environ.get("BACKUP_MAX_FILES", "200000"))


@dataclass(frozen=True)
class ArchiveSources:
    homepage_data: Path
    obsidian_note: Path
    native_config: Path


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
        if any(part.startswith(".restore-") for part in relative.parts):
            continue
        copied = target / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, copied)
        result.append(copied)
    return result


def _copy_sqlite(source: Path, target: Path, label: str) -> None:
    if not source.is_file():
        raise ArchiveError(f"{label} database does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(target)
    try:
        with source_db:
            source_db.backup(target_db)
    except sqlite3.Error as exc:
        raise ArchiveError(f"{label} database could not be copied") from exc
    finally:
        source_db.close()
        target_db.close()


def build_archive(job_dir: Path, sources: ArchiveSources) -> Path:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = job_dir / "payload"
    payload.mkdir()
    files: list[tuple[str, Path]] = []
    try:
        data_source = Path(sources.homepage_data)
        if not data_source.is_dir():
            raise ArchiveError("首页数据目录不存在")
        homepage_target = payload / "homepage"
        _copy_sqlite(data_source / "bookmarks.db", homepage_target / "bookmarks.db", "书签")
        _copy_sqlite(data_source / "state.db", homepage_target / "state.db", "首页状态")
        files.extend([( "homepage/bookmarks.db", homepage_target / "bookmarks.db"),
                      ("homepage/state.db", homepage_target / "state.db")])
        for name in ("site-icons", "note-conflicts"):
            source = data_source / name
            target = homepage_target / name
            if source.exists():
                copied = _copy_tree(source, target, f"homepage/{name}")
                files.extend((f"homepage/{item.relative_to(homepage_target).as_posix()}", item) for item in copied)
        note = Path(sources.obsidian_note)
        config = Path(sources.native_config)
        if not note.is_file() or note.is_symlink():
            raise ArchiveError("Obsidian 笔记不存在")
        if not config.is_file() or config.is_symlink():
            raise ArchiveError("原生服务配置不存在")
        note_target = payload / "homepage" / "浏览器首页笔记.md"
        note_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(note, note_target)
        config_target = payload / "deployment" / "native-config.json"
        config_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config, config_target)
        files.extend([( "homepage/浏览器首页笔记.md", note_target),
                      ("deployment/native-config.json", config_target)])
        files.sort(key=lambda row: row[0])
        manifest = {
            "formatVersion": FORMAT_VERSION,
            "storageBackend": "sqlite",
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "applicationVersion": "1",
            "files": [{"path": name, "size": path.stat().st_size, "sha256": _hash(path)} for name, path in files],
        }
        (payload / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (payload / "RESTORE.txt").write_text(
            "浏览器首页本地备份\n\n此 ZIP 包含首页 SQLite 数据库、网站图标、冲突副本、Obsidian 笔记和原生服务配置。\n"
            "旧版本备份不属于此格式。请像保管密码文件一样保护本文件。\n", encoding="utf-8")
        archive = job_dir / "backup.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as output:
            output.writestr("homepage/site-icons/", "")
            output.writestr("homepage/note-conflicts/", "")
            output.write(payload / "RESTORE.txt", "RESTORE.txt")
            for name, source in files:
                output.write(source, name)
            output.write(payload / "manifest.json", "manifest.json")
        return archive
    except ArchiveError:
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise ArchiveError("备份过程中读取或写入文件失败") from exc


def _safe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and "\\" not in name and not path.is_absolute() and ".." not in path.parts \
        and not name.startswith("/") and not name.startswith("//") and ":" not in path.parts[0] \
        and path.as_posix() == name.rstrip("/")


def validate_archive(archive: Path, extract_to: Path) -> dict:
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
            allowed_dirs = {"homepage/site-icons/", "homepage/note-conflicts/"}
            for entry in entries:
                name = entry.filename
                mode = entry.external_attr >> 16
                if not _safe_entry(name) or stat.S_ISLNK(mode) or name in seen:
                    raise ArchiveError("备份文件包含不安全路径")
                allowed = name in {"manifest.json", "RESTORE.txt", "homepage/bookmarks.db", "homepage/state.db",
                                   "homepage/浏览器首页笔记.md", "deployment/native-config.json"} \
                    or name.startswith("homepage/site-icons/") or name.startswith("homepage/note-conflicts/")
                if name.endswith("/"):
                    allowed = name in allowed_dirs
                if not allowed:
                    raise ArchiveError("备份文件包含不支持的项目")
                seen.add(name)
                expanded_size += entry.file_size
                if expanded_size > MAX_EXPANDED_BYTES:
                    raise ArchiveError("备份解压后超过允许大小")
            if "manifest.json" not in seen or "RESTORE.txt" not in seen:
                raise ArchiveError("备份文件缺少清单")
            extract_to.mkdir(parents=True, exist_ok=False)
            created = True
            for entry in entries:
                name = entry.filename
                target = extract_to.joinpath(*PurePosixPath(name).parts)
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
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
            or manifest.get("storageBackend") != "sqlite" or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("createdAt"), str)):
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("不支持此备份格式")
    manifest_files: dict[str, dict] = {}
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
        if not _safe_entry(name):
            shutil.rmtree(extract_to, ignore_errors=True)
            raise ArchiveError("备份清单包含不安全路径")
        file_path = extract_to.joinpath(*PurePosixPath(name).parts)
        if (not file_path.is_file() or not isinstance(item.get("size"), int)
                or file_path.stat().st_size != item.get("size") or not isinstance(item.get("sha256"), str)
                or _hash(file_path) != item.get("sha256")):
            shutil.rmtree(extract_to, ignore_errors=True)
            raise ArchiveError("备份文件校验失败")
    required = {"homepage/bookmarks.db", "homepage/state.db", "homepage/浏览器首页笔记.md", "deployment/native-config.json"}
    if not required.issubset(manifest_files):
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("备份文件缺少必要内容")
    try:
        for name in ("homepage/state.db", "homepage/bookmarks.db"):
            db = sqlite3.connect(extract_to / name)
            try:
                integrity = db.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise ArchiveError("首页数据库损坏")
            finally:
                db.close()
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(extract_to, ignore_errors=True)
        raise ArchiveError("无法验证首页数据库") from exc
    return manifest
