from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import (
    PayloadTooLarge,
    UnsafeFileRejected,
    UnsupportedMediaType,
    ValidationFailed,
)
from app.storage.validators import sanitize_file_name, validate_web_content_safety

Resolver = Callable[[str, int | None], Sequence[str]]

_METADATA_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
}


@dataclass(frozen=True)
class UrlFetchResult:
    requested_url: str
    final_url: str
    safe_source_url: str
    safe_final_url: str
    content: bytes
    content_type: str
    file_name: str
    fetched_at: datetime
    redirect_count: int


@dataclass(frozen=True)
class _ValidatedUrl:
    url: str
    connect_urls: tuple[str, ...]
    host_header: str
    sni_hostname: str | None


class UrlFetchService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self.resolver = resolver or _default_resolver

    def fetch(self, url: str) -> UrlFetchResult:
        current_url = _validate_url(url, settings=self.settings, resolver=self.resolver)
        redirects = 0
        owns_client = self.client is None
        client = self.client or httpx.Client(
            timeout=self.settings.document_url_fetch_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": self.settings.document_url_fetch_user_agent},
        )
        try:
            while True:
                with _safe_stream(client, current_url) as response:
                    if _is_redirect(response.status_code):
                        if redirects >= self.settings.document_url_fetch_max_redirects:
                            raise ValidationFailed(
                                details=[{"field": "url", "reason": "Too many redirects."}]
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise ValidationFailed(
                                details=[
                                    {"field": "url", "reason": "Redirect location is missing."}
                                ]
                            )
                        try:
                            redirect_url = str(httpx.URL(current_url.url).join(location))
                        except httpx.InvalidURL as exc:
                            raise ValidationFailed(
                                details=[{"field": "url", "reason": "Invalid redirect URL."}]
                            ) from exc
                        current_url = _validate_url(
                            redirect_url,
                            settings=self.settings,
                            resolver=self.resolver,
                        )
                        redirects += 1
                        continue

                    if response.status_code < 200 or response.status_code >= 300:
                        raise ValidationFailed(
                            details=[{"field": "url", "reason": "URL fetch failed."}]
                        )
                    content_type = _validate_content_type(
                        response.headers.get("content-type"),
                        settings=self.settings,
                    )
                    content = _read_limited(
                        response,
                        max_bytes=self.settings.document_url_fetch_max_bytes,
                    )
                    validate_web_content_safety(content_type=content_type, content=content)
                    return UrlFetchResult(
                        requested_url=url,
                        final_url=current_url.url,
                        safe_source_url=redact_url_for_display(url),
                        safe_final_url=redact_url_for_display(current_url.url),
                        content=content,
                        content_type=content_type,
                        file_name=_file_name_for_url(current_url.url, content_type),
                        fetched_at=datetime.now(UTC),
                        redirect_count=redirects,
                    )
        finally:
            if owns_client:
                client.close()


def redact_url_for_display(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "redacted"
    host = (parsed.hostname or "").lower()
    if not host:
        return "redacted"
    try:
        parsed_port = parsed.port
    except ValueError:
        return "redacted"
    port = f":{parsed_port}" if parsed_port is not None else ""
    host = _host_for_netloc(host)
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def _validate_url(url: str, *, settings: Settings, resolver: Resolver) -> _ValidatedUrl:
    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise ValidationFailed(details=[{"field": "url", "reason": "Invalid URL."}]) from exc
    scheme = parsed.scheme.lower()
    if scheme not in settings.document_url_fetch_allowed_schemes:
        raise ValidationFailed(details=[{"field": "url", "reason": "URL scheme is not allowed."}])
    if parsed.username or parsed.password:
        raise ValidationFailed(
            details=[{"field": "url", "reason": "URL credentials are not allowed."}]
        )
    if not parsed.hostname:
        raise ValidationFailed(details=[{"field": "url", "reason": "URL host is required."}])
    hostname = _normalized_hostname(parsed.hostname)
    _validate_hostname_policy(hostname)
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise ValidationFailed(details=[{"field": "url", "reason": "Invalid URL port."}]) from exc
    port = explicit_port or (443 if scheme == "https" else 80)
    resolved_addresses = [
        _validate_ip_policy(
            address,
            block_private=settings.document_url_fetch_block_private_ips,
        )
        for address in resolver(hostname, port)
    ]
    if not resolved_addresses:
        raise UnsafeFileRejected()
    normalized_netloc = _host_for_netloc(hostname)
    if explicit_port is not None:
        normalized_netloc = f"{normalized_netloc}:{explicit_port}"
    path = parsed.path or "/"
    normalized_url = urlunsplit((scheme, normalized_netloc, path, parsed.query, ""))
    connect_urls = tuple(
        urlunsplit(
            (
                scheme,
                _connect_netloc_for_address(address, explicit_port=explicit_port),
                path,
                parsed.query,
                "",
            )
        )
        for address in resolved_addresses
    )
    return _ValidatedUrl(
        url=normalized_url,
        connect_urls=connect_urls,
        host_header=normalized_netloc,
        sni_hostname=hostname if scheme == "https" else None,
    )


def _validate_hostname_policy(hostname: str) -> None:
    lowered = hostname.rstrip(".").lower()
    if lowered in _METADATA_HOSTNAMES or lowered == "localhost" or lowered.endswith(".localhost"):
        raise UnsafeFileRejected()
    if lowered.endswith(".local"):
        raise UnsafeFileRejected()


def _validate_ip_policy(address: str, *, block_private: bool) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise UnsafeFileRejected() from exc
    if ip == ipaddress.ip_address("169.254.169.254"):
        raise UnsafeFileRejected()
    if block_private and not ip.is_global:
        raise UnsafeFileRejected()
    return ip.compressed


def _default_resolver(hostname: str, port: int | None) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailed(
            details=[{"field": "url", "reason": "URL host cannot be resolved."}]
        ) from exc
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        address = str(info[4][0])
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    if not addresses:
        raise UnsafeFileRejected()
    return addresses


@contextmanager
def _safe_stream(client: httpx.Client, target: _ValidatedUrl) -> Iterator[httpx.Response]:
    extensions = {"sni_hostname": target.sni_hostname} if target.sni_hostname else None
    last_exc: httpx.InvalidURL | httpx.HTTPError | None = None
    for connect_url in target.connect_urls:
        try:
            stream = client.stream(
                "GET",
                connect_url,
                follow_redirects=False,
                headers={"Host": target.host_header},
                extensions=extensions,
            )
            response = stream.__enter__()
        except (httpx.InvalidURL, httpx.HTTPError) as exc:
            last_exc = exc
            continue
        try:
            yield response
        except BaseException as exc:
            if stream.__exit__(type(exc), exc, exc.__traceback__):
                return
            if isinstance(exc, httpx.TimeoutException):
                raise ValidationFailed(
                    details=[{"field": "url", "reason": "URL fetch timed out."}]
                ) from exc
            if isinstance(exc, httpx.InvalidURL):
                raise ValidationFailed(
                    details=[{"field": "url", "reason": "Invalid URL."}]
                ) from exc
            if isinstance(exc, httpx.HTTPError):
                raise ValidationFailed(
                    details=[{"field": "url", "reason": "URL fetch failed."}]
                ) from exc
            raise
        else:
            stream.__exit__(None, None, None)
        return
    if isinstance(last_exc, httpx.TimeoutException):
        raise ValidationFailed(
            details=[{"field": "url", "reason": "URL fetch timed out."}]
        ) from last_exc
    if isinstance(last_exc, httpx.InvalidURL):
        raise ValidationFailed(details=[{"field": "url", "reason": "Invalid URL."}]) from last_exc
    raise ValidationFailed(details=[{"field": "url", "reason": "URL fetch failed."}]) from last_exc


def _validate_content_type(value: str | None, *, settings: Settings) -> str:
    if not value:
        raise UnsupportedMediaType()
    content_type = value.split(";", 1)[0].strip().lower()
    if content_type not in settings.document_url_fetch_allowed_content_types:
        raise UnsupportedMediaType()
    return content_type


def _read_limited(response: httpx.Response, *, max_bytes: int) -> bytes:
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge()
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise ValidationFailed(details=[{"field": "url", "reason": "Fetched body is empty."}])
    return content


def _file_name_for_url(url: str, content_type: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or "document"
    path_name = parsed.path.rsplit("/", 1)[-1].strip() or "index"
    stem = path_name.rsplit(".", 1)[0] or "index"
    extension = ".xml" if _is_xml_content_type(content_type) else ".html"
    return sanitize_file_name(f"{host}-{stem}{extension}")


def _is_xml_content_type(content_type: str) -> bool:
    return content_type in {
        "text/xml",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }


def _is_redirect(status_code: int) -> bool:
    return status_code in {301, 302, 303, 307, 308}


def _normalized_hostname(hostname: str) -> str:
    try:
        return ipaddress.ip_address(hostname.rstrip(".")).compressed.lower()
    except ValueError:
        pass
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValidationFailed(details=[{"field": "url", "reason": "Invalid URL host."}]) from exc


def _host_for_netloc(hostname: str) -> str:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    if ip.version == 6:
        return f"[{ip.compressed}]"
    return ip.compressed


def _connect_netloc_for_address(address: str, *, explicit_port: int | None) -> str:
    connect_netloc = _host_for_netloc(address)
    if explicit_port is not None:
        connect_netloc = f"{connect_netloc}:{explicit_port}"
    return connect_netloc
