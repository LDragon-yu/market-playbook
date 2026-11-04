"""Best-effort public HTML intake, with pinned public IPs and redirect validation.

No cookies, login bypass, scripts, media downloading or private-network access.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


VIDEO_DOMAINS = ("bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com", "kuaishou.com",
                 "youtube.com", "youtu.be", "ixigua.com")


def validate_url(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("只接受不含用户名、密码的公开 http/https 链接。")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("链接只允许标准网页端口。")
    if len(url) > 3000 or any(ord(c) < 32 for c in url):
        raise ValueError("链接过长或包含控制字符。")
    return parts.scheme, parts.hostname.encode("idna").decode(), port


def is_video_url(url: str) -> bool:
    _, host, _ = validate_url(url)
    return any(host == d or host.endswith("." + d) for d in VIDEO_DOMAINS) or bool(
        re.search(r"\.(mp4|mov|m3u8)(?:[?#]|$)", url, re.I))


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except OSError:
        raise ValueError("链接域名无法解析；请直接发送正文。") from None
    if not addresses:
        raise ValueError("域名没有可用地址。")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global or (address.version == 6 and address.ipv4_mapped and not address.ipv4_mapped.is_global):
            raise ValueError("拒绝访问本机、内网、保留地址或云服务器元数据地址。")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        self.sock = self._context.wrap_socket(socket.create_connection((self.address, self.port), self.timeout),
                                              server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=10)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.in_title = False
        self.title: list[str] = []
        self.text: list[str] = []
        self.video = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in {"video", "audio"}:
            self.video = True
        if tag in {"p", "br", "div", "article", "h1", "h2", "li"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer"}:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        elif not self.skip:
            self.text.append(data)


def fetch_article(url: str) -> tuple[str, str]:
    current = url
    for attempt in range(4):
        scheme, host, port = validate_url(current)
        if is_video_url(current):
            raise ValueError("视频链接需要字幕或转写文本；未观看、未转写视频。")
        address = public_addresses(host, port)[0]
        connection = (_PinnedHTTPS if scheme == "https" else _PinnedHTTP)(host, port, address)
        try:
            parts = urlsplit(current)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={"User-Agent": "JianshiResearch/0.4", "Accept": "text/html"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = response.getheader("Location")
                if not target or attempt == 3:
                    raise ValueError("链接重定向过多或无目标。")
                current = urljoin(current, target)
                continue
            if response.status != 200:
                raise ValueError(f"网页无法直接读取（HTTP {response.status}），请提供正文。")
            content_type = response.getheader("Content-Type", "").lower()
            if "text/html" not in content_type or response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("暂不支持该网页格式，请提供正文或字幕。")
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("网页过大，请直接发送正文。")
            match = re.search(r"charset=([\w-]+)", content_type)
            encoding = match.group(1) if match else "utf-8"
            try:
                html = raw.decode(encoding, errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            parser = _ArticleParser()
            parser.feed(html)
            title = "".join(parser.title).strip()[:200] or "公开网页素材"
            text = "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())[:30000]
            if parser.video or len(text) < 300 or any(x in title for x in ("验证码", "访问受限", "安全验证", "登录")):
                raise ValueError("未取得可信正文（可能需要登录、脚本或字幕），请直接发送正文。")
            return title, text
        except (OSError, http.client.HTTPException):
            raise ValueError("公开网页读取失败；请直接发送正文或字幕。") from None
        finally:
            connection.close()
    raise ValueError("无法读取该链接。")
