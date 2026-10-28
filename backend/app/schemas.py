from typing import Any, Literal
from urllib.parse import urlsplit
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in value):
        raise ValueError("A valid HTTP(S) URL without credentials is required")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkUpdate(StrictModel):
    name: str | None = Field(default=None, max_length=2048)
    url: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=2048)
    icon: str | None = Field(default=None, max_length=50)
    iconWeight: Literal["thin", "light", "regular", "bold", "fill", "duotone"] | None = None
    color: str | None = None
    collectionId: int | None = Field(default=None, gt=0)
    expected: dict[str, Any]

    @field_validator("url")
    @classmethod
    def url_safe(cls, value):
        return safe_url(value) if value is not None else value

    @field_validator("icon")
    @classmethod
    def glyph_safe(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]*", value):
            raise ValueError("Invalid icon glyph")
        return value

    @field_validator("color")
    @classmethod
    def color_safe(cls, value):
        if value is not None and not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value):
            raise ValueError("Use a hex color")
        return value

    @model_validator(mode="after")
    def require_base(self):
        changed = self.model_fields_set - {"expected"}
        if not changed or not changed <= self.expected.keys():
            raise ValueError("Supply the original value in expected for every changed field")
        if any(getattr(self, key) is None for key in changed if key not in {"icon", "iconWeight", "color"}):
            raise ValueError("This field cannot be null")
        return self


class LinkLayout(StrictModel):
    showName: bool | None = None
    showIcon: bool | None = None
    showDescription: bool | None = None
    order: int | None = Field(default=None, ge=0)


class CollectionLayout(StrictModel):
    collapsed: bool | None = None
    order: int | None = Field(default=None, ge=0)


class SearchProvider(StrictModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=8192)

    @field_validator("url")
    @classmethod
    def template_safe(cls, value):
        safe_url(value)
        parsed = urlsplit(value)
        if value.count("{query}") != 1 or "{" in parsed.netloc or "}" in parsed.netloc:
            raise ValueError("Template must contain one {query} outside the hostname")
        return value


class SearchSettings(StrictModel):
    providers: list[SearchProvider] = Field(min_length=1, max_length=30)
    activeProviderId: str

    @model_validator(mode="after")
    def valid_active(self):
        ids = [provider.id for provider in self.providers]
        if len(ids) != len(set(ids)) or self.activeProviderId not in ids:
            raise ValueError("Provider IDs must be unique and the active provider must exist")
        return self
