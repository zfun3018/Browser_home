import hashlib
import json
from pathlib import Path

import anyio
import pytest

from backend.app.site_assets import SiteAssetService, SiteIconNotFound


def test_get_icon_migrates_legacy_full_url_cache(tmp_path: Path, monkeypatch):
    service = SiteAssetService(tmp_path)
    raw_url = "https://sub.example.com/deep/page?q=1"
    legacy_key = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
    data = b"legacy-icon"
    (tmp_path / f"{legacy_key}.bin").write_bytes(data)
    (tmp_path / f"{legacy_key}.json").write_text(
        json.dumps({"contentType": "image/png", "savedAt": 4_000_000_000}),
        encoding="utf-8",
    )

    async def fail_if_fetched(_url):
        raise AssertionError("cache migration should avoid a network fetch")

    monkeypatch.setattr(service, "_find_icon", fail_if_fetched)

    assert anyio.run(service.get_icon, raw_url) == (data, "image/png")


def test_get_icon_caches_missing_result(tmp_path: Path, monkeypatch):
    service = SiteAssetService(tmp_path)
    raw_url = "https://missing.example.com/"

    async def missing(_url):
        raise SiteIconNotFound("missing")

    monkeypatch.setattr(service, "_find_icon", missing)
    with pytest.raises(SiteIconNotFound):
        anyio.run(service.get_icon, raw_url)

    async def fail_if_fetched(_url):
        raise AssertionError("negative cache should avoid a network fetch")

    monkeypatch.setattr(service, "_find_icon", fail_if_fetched)
    with pytest.raises(SiteIconNotFound):
        anyio.run(service.get_icon, raw_url)
