from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import (
    PayloadTooLarge,
    UnsafeFileRejected,
    UnsupportedMediaType,
    ValidationFailed,
)
from app.services.url_fetch_service import UrlFetchService, redact_url_for_display


def test_url_fetch_success_uses_safe_source_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_URL_FETCH_MAX_BYTES", "1024")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><h1>Safe</h1></body></html>",
        )

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: ["93.184.216.34"],
    )

    result = service.fetch("https://example.com/page?token=secret")

    assert result.safe_source_url == "https://example.com/page"
    assert result.safe_final_url == "https://example.com/page"
    assert result.content_type == "text/html"
    assert result.file_name == "example.com-page.html"
    assert b"Safe" in result.content
    get_settings.cache_clear()


def test_url_fetch_preserves_ipv6_literal_brackets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://[2606:4700:4700::1111]/"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>IPv6</body></html>",
        )

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: [host],
    )

    result = service.fetch("https://[2606:4700:4700::1111]/")

    assert result.final_url == "https://[2606:4700:4700::1111]/"
    assert result.safe_final_url == "https://[2606:4700:4700::1111]/"


def test_url_fetch_tries_next_validated_address_after_connect_failure() -> None:
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(str(request.url.host))
        if request.url.host == "2001:4860:4860::8888":
            raise httpx.ConnectError("unreachable", request=request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>fallback address</body></html>",
        )

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: ["2001:4860:4860::8888", "93.184.216.34"],
    )

    result = service.fetch("https://example.com/")

    assert result.content == b"<html><body>fallback address</body></html>"
    assert seen_hosts == ["2001:4860:4860::8888", "93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:password@example.com/",
        "https://example.com:bad/",
    ],
)
def test_url_fetch_rejects_disallowed_scheme_or_userinfo(url: str) -> None:
    service = UrlFetchService(resolver=lambda host, port: ["93.184.216.34"])

    with pytest.raises(ValidationFailed):
        service.fetch(url)


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://localhost/page", ["127.0.0.1"]),
        ("http://docs.local/page", ["93.184.216.34"]),
        ("http://example.com/page", ["10.0.0.1"]),
        ("http://example.com/page", ["::1"]),
        ("http://example.com/page", ["169.254.169.254"]),
    ],
)
def test_url_fetch_rejects_private_local_and_metadata_targets(
    url: str,
    addresses: list[str],
) -> None:
    service = UrlFetchService(resolver=lambda host, port: addresses)

    with pytest.raises(UnsafeFileRejected):
        service.fetch(url)


def test_url_fetch_revalidates_redirect_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"location": "http://localhost/private"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"blocked")

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"],
    )

    with pytest.raises(UnsafeFileRejected):
        service.fetch("https://example.com/start")


def test_url_fetch_rejects_malformed_redirect_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://[::1"})

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: ["93.184.216.34"],
    )

    with pytest.raises(ValidationFailed):
        service.fetch("https://example.com/start")


def test_url_fetch_enforces_redirect_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_URL_FETCH_MAX_REDIRECTS", "1")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"https://example.com{request.url.path}x"})

    service = UrlFetchService(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda host, port: ["93.184.216.34"],
    )

    with pytest.raises(ValidationFailed):
        service.fetch("https://example.com/a")
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/xml", b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><x />"""),
        (
            "application/xhtml+xml",
            b'<x:svg xmlns:x="http://www.w3.org/2000/svg"><x:text>unsafe</x:text></x:svg>',
        ),
    ],
)
def test_url_fetch_rejects_unsafe_xml_or_xhtml_body(
    content_type: str,
    body: bytes,
) -> None:
    service = UrlFetchService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": content_type},
                    content=body,
                )
            )
        ),
        resolver=lambda host, port: ["93.184.216.34"],
    )

    with pytest.raises(UnsafeFileRejected):
        service.fetch("https://example.com/feed")


def test_url_fetch_enforces_content_type_and_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_URL_FETCH_MAX_BYTES", "1024")
    get_settings.cache_clear()
    too_large_service = UrlFetchService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    content=b"x" * 1025,
                )
            )
        ),
        resolver=lambda host, port: ["93.184.216.34"],
    )
    with pytest.raises(PayloadTooLarge):
        too_large_service.fetch("https://example.com/large")

    bad_type_service = UrlFetchService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/octet-stream"},
                    content=b"binary",
                )
            )
        ),
        resolver=lambda host, port: ["93.184.216.34"],
    )
    with pytest.raises(UnsupportedMediaType):
        bad_type_service.fetch("https://example.com/file")
    get_settings.cache_clear()


def test_url_redaction_removes_query_and_userinfo() -> None:
    assert redact_url_for_display("https://user:secret@example.com/path?token=abc#frag") == (
        "https://example.com/path"
    )
