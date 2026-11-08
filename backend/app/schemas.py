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


class CaptureRequest(StrictModel):
    url: str = Field(max_length=8192)
    title: str = Field(default="", max_length=2048)
    description: str = Field(default="", max_length=2048)
    collectionId: int | None = Field(default=None, gt=0)

    @field_validator("url")
    @classmethod
    def url_safe(cls, value):
        return safe_url(value)


class LinkLayout(StrictModel):
    showName: bool | None = None
    showIcon: bool | None = None
    showDescription: bool | None = None
    order: int | None = Field(default=None, ge=0)


class LinkOrder(StrictModel):
    id: int = Field(gt=0)
    order: int = Field(ge=0)


class LinkOrdersUpdate(StrictModel):
    orders: list[LinkOrder] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [entry.id for entry in self.orders]
        if len(ids) != len(set(ids)):
            raise ValueError("Link IDs must be unique")
        return self


class CollectionLayout(StrictModel):
    collapsed: bool | None = None
    order: int | None = Field(default=None, ge=0)
    displayMode: Literal["grid", "list"] | None = None


class CollectionPassword(StrictModel):
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def password_safe(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("密码不能包含控制字符")
        return value


class CollectionUnlock(StrictModel):
    password: str = Field(min_length=1, max_length=128)


class CollectionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("分组名称不能为空")
        return value


class CollectionUpdate(CollectionCreate):
    pass


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
