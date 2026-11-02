"""Environment configuration for the local SQLite homepage runtime."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    backup_service_url: str = "http://127.0.0.1:8010"
    bookmark_db_path: Path = Path("data/bookmarks.db")
    obsidian_note_path: Path = Path("data/浏览器首页笔记.md")
    capture_token_file: Path | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:3011", "http://127.0.0.1:3011")

    @classmethod
    def from_env(cls):
        data_dir = Path(os.environ.get("HOMEPAGE_DATA_DIR", "data"))
        return cls(data_dir=data_dir,
                   backup_service_url=os.environ.get("BACKUP_SERVICE_URL", "http://127.0.0.1:8010"),
                   bookmark_db_path=Path(os.environ.get("BOOKMARK_DB_PATH", str(data_dir / "bookmarks.db"))),
                   obsidian_note_path=Path(os.environ.get("OBSIDIAN_NOTE_PATH", str(data_dir / "浏览器首页笔记.md"))),
                   capture_token_file=Path(os.environ["CAPTURE_TOKEN_FILE"]) if os.environ.get("CAPTURE_TOKEN_FILE") else None)

    def read_capture_token(self) -> str | None:
        if self.capture_token_file is None:
            return None
        try:
            return self.capture_token_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
