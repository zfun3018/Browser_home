import socket

import anyio

from backend.app.metadata import assert_public_url, main_domain_url


def test_main_domain_url_discards_paths_and_common_subdomains():
    assert main_domain_url("https://www.shop.example.com/products?id=1") == "https://example.com/"
    assert main_domain_url("https://a.example.co.uk/path") == "https://example.co.uk/"


def test_main_domain_url_preserves_non_default_port_for_local_testing():
    assert main_domain_url("http://www.example.test:8080/deep/page") == "http://example.test:8080/"


def test_assert_public_url_allows_flclash_fake_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.245", 443))],
    )

    anyio.run(assert_public_url, "https://example.com/")
