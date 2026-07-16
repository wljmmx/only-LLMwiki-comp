"""SSRF 防护工具（P0-3 安全加固）

提供 URL 安全验证函数，防止服务端请求伪造（SSRF）：
- 白名单 scheme（仅 http/https）
- 内网 IP 黑名单（私有/环回/链路本地/保留/多播）
- DNS 解析后二次检查（防止域名指向内部 IP）

设计为独立模块，可从 HTTP 端点和后台任务（webhook/ollama/rollback）复用。

测试环境：设置 OPSKG_ALLOW_LOOPBACK_URLS=1 允许环回地址（用于 mock server 测试）。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


class SsrfError(ValueError):
    """SSRF 防护拒绝异常"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# 禁止访问的 IP 段（私有/环回/链路本地/保留地址）
_BLOCKED_IP_PREFIXES = (
    "127.",  # IPv4 loopback
    "10.",  # private A
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
    "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",  # private B
    "192.168.",  # private C
    "169.254.",  # link-local
    "::1",  # IPv6 loopback
    "fc", "fd",  # IPv6 ULA
    "fe80",  # IPv6 link-local
)


def _is_blocked_ip(ip_str: str) -> bool:
    """检查 IP 是否在禁止访问的私有/内部段"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        # 非 IP 地址（如 hostname），做前缀检查兜底
        return any(ip_str.startswith(p) for p in _BLOCKED_IP_PREFIXES)


def validate_url_safe(url: str) -> str:
    """验证 URL 安全性，防止 SSRF（服务端请求伪造）

    检查项：
    1. scheme 必须是 http 或 https
    2. 解析 hostname，若为 IP 则检查是否在私有/内部段
    3. 若为域名，做 DNS 解析后检查所有解析结果

    通过验证返回原 URL，否则抛出 SsrfError。

    测试环境：设置 OPSKG_ALLOW_LOOPBACK_URLS=1 允许环回地址。

    用法：
        from app.core.security import validate_url_safe

        try:
            validated_url = validate_url_safe(user_url)
        except SsrfError as e:
            raise HTTPException(400, str(e))
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfError(f"不允许的 URL scheme: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if not hostname:
        raise SsrfError("URL 缺少 hostname")

    # 测试环境：允许环回地址（用于 mock server 测试）
    if os.environ.get("OPSKG_ALLOW_LOOPBACK_URLS") == "1":
        return url

    # 直接检查 IP 字面量
    if _is_blocked_ip(hostname):
        raise SsrfError(f"禁止访问私有/内部地址: {hostname}")

    # DNS 解析检查（防止域名指向内部 IP）
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
        for ai in addr_infos:
            ip = ai[4][0]
            if _is_blocked_ip(ip):
                raise SsrfError(
                    f"域名 {hostname} 解析到内部地址 {ip}，禁止访问"
                )
    except socket.gaierror:
        pass  # DNS 解析失败，允许通过（连接时会自然失败）

    return url
