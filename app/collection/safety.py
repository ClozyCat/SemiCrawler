from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    host: str
    addresses: tuple[str, ...]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def validate_public_address(address: str) -> None:
    if not _is_public(address):
        raise UnsafeUrlError(f"连接对端是非公网地址: {address}")


def validate_url(url: str, allowed_hosts: set[str] | None = None) -> ValidatedUrl:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("仅允许 http 和 https URL")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL 不得包含用户名或密码")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("URL 缺少有效主机")
    normalized_allowed = {item.rstrip(".").lower() for item in allowed_hosts or set()}
    if normalized_allowed and host not in normalized_allowed:
        raise UnsafeUrlError(f"主机未获允许: {host}")
    try:
        infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"主机无法解析: {host}") from exc
    addresses = tuple(dict.fromkeys(item[4][0] for item in infos))
    if not addresses or any(not _is_public(address) for address in addresses):
        raise UnsafeUrlError(f"主机解析到非公网地址: {host}")
    return ValidatedUrl(url=url, host=host, addresses=addresses)
