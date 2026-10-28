"""Environment configuration; credentials are exclusively file backed."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    linkwarden_url: str = "http://linkwarden:3000"
    token_file: Path | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:3011", "http://127.0.0.1:3011")

    @classmethod
    def from_env(cls):
        token_path = os.environ.get("LINKWARDEN_API_TOKEN_FILE")
        return cls(data_dir=Path(os.environ.get("HOMEPAGE_DATA_DIR", "data")),
                   linkwarden_url=os.environ.get("LINKWARDEN_URL", "http://linkwarden:3000"),
                   token_file=Path(token_path) if token_path else None)

    def read_token(self) -> str | None:
        if self.token_file is None:
            return None
        try:
            return self.token_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
