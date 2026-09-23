# -*- coding: utf-8 -*-
"""Per-client residential egress.

Chrome on the VPS talks to a local forwarder with no credentials. The forwarder
authenticates to one upstream proxy and keeps that assignment for the same
Sendline user. Another user gets another upstream.

Two ways to supply exits, via data/proxies.txt (Manage users) or env:

- Static lines, one dedicated proxy each:
  http://user:pass@host:port
  host:port:user:pass
- One sticky gateway line containing {session}. Sendline stores a different
  session token per client and reuses it, which is how providers keep one IP.
"""
from __future__ import annotations

import base64
import os
import secrets
import select
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import ProxyHandler, Request, build_opener

import db
import vault

POOL_FILE = db.DATA_DIR / "proxies.txt"
_MAX_POOL_BYTES = 100_000


class EgressError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedProxy:
    host: str
    port: int
    username: str = ""
    password: str = ""
    mode: str = "static"

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def identity(self) -> tuple[str, int, str]:
        """Same slot when the username matches, so a password rotation stays pinned."""
        return (self.host, self.port, self.username)

    @property
    def credential(self) -> tuple[str, int, str, str]:
        return (self.host, self.port, self.username, self.password)

    def as_url(self) -> str:
        auth = ""
        if self.username or self.password:
            auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"http://{auth}{self.host}:{self.port}"

    def masked(self) -> str:
        if self.username:
            return f"http://{self.username}:***@{self.host}:{self.port}"
        return f"http://{self.host}:{self.port}"


def _clean_port(raw: str) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise EgressError("Proxy port must be a number.") from exc
    if port < 1 or port > 65535:
        raise EgressError("Proxy port must be between 1 and 65535.")
    return port


def parse_proxy_line(line: str, *, mode: str = "static") -> ParsedProxy:
    text = (line or "").strip()
    if not text:
        raise EgressError("Empty proxy line.")
    if "://" not in text:
        parts = text.split(":")
        if len(parts) == 2:
            host, port_s = parts
            user, password = "", ""
        elif len(parts) >= 4:
            host, port_s, user = parts[0], parts[1], parts[2]
            password = ":".join(parts[3:])
        else:
            raise EgressError("Use host:port:user:pass or http://user:pass@host:port.")
        if not host or any(c.isspace() for c in host):
            raise EgressError("Proxy host is missing.")
        return ParsedProxy(host, _clean_port(port_s), user, password, mode)

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise EgressError("Only HTTP proxies are supported (http://user:pass@host:port).")
    host = parsed.hostname or ""
    if not host:
        raise EgressError("Proxy host is missing.")
    port = parsed.port or (443 if parsed.scheme == "https" else 8080)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    return ParsedProxy(host, int(port), username, password, mode)


def _template_from_env() -> str:
    return (os.environ.get("SENDLINE_PROXY_TEMPLATE") or "").strip()


def parse_pool_text(text: str) -> tuple[list[ParsedProxy], str]:
    """Return static proxies and a sticky template (may be empty)."""
    if len(text.encode("utf-8")) > _MAX_POOL_BYTES:
        raise EgressError("Proxy list is too large.")
    static: list[ParsedProxy] = []
    template = ""
    seen: set[tuple[str, int, str]] = set()
    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("template "):
            line = line.split(None, 1)[1].strip()
            is_template = True
        else:
            is_template = "{session}" in line
        if is_template:
            if template:
                raise EgressError("Only one {session} gateway line is allowed.")
            if "{session}" not in line:
                raise EgressError("A gateway line must include {session}.")
            sample = line.replace("{session}", "sessiontoken")
            try:
                parse_proxy_line(sample, mode="sticky")
            except EgressError as exc:
                raise EgressError(f"Line {index}: {exc}") from exc
            template = line
            continue
        try:
            proxy = parse_proxy_line(line, mode="static")
        except EgressError as exc:
            raise EgressError(f"Line {index}: {exc}") from exc
        if proxy.identity in seen:
            raise EgressError(f"Line {index} repeats {proxy.label} for the same username.")
        seen.add(proxy.identity)
        static.append(proxy)
    return static, template


def pool_path() -> Path:
    override = (os.environ.get("SENDLINE_PROXY_FILE") or "").strip()
    if override:
        return Path(override)
    return POOL_FILE


def load_pool_text() -> str:
    path = pool_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EgressError(f"Could not read the proxy list: {exc}") from exc


def load_pool() -> tuple[list[ParsedProxy], str]:
    text = load_pool_text()
    static, template = parse_pool_text(text)
    if not template:
        template = _template_from_env()
        if template:
            parse_pool_text("template " + template)
    return static, template


def save_pool_text(text: str) -> None:
    parse_pool_text(text)
    path = pool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text if (not text or text.endswith("\n")) else text + "\n"
    path.write_text(normalized, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def configured() -> bool:
    try:
        static, template = load_pool()
    except EgressError:
        return True
    return bool(static or template)


def _stored_proxy(user_id: int) -> ParsedProxy | None:
    row = db.get_user_proxy(user_id)
    cipher = (row or {}).get("proxy_ciphertext") or ""
    if not cipher:
        return None
    try:
        url = vault.decrypt_password(cipher)
    except ValueError as exc:
        raise EgressError(
            "Saved residential proxy could not be decrypted, so Chrome was not started."
        ) from exc
    mode = (row or {}).get("proxy_mode") or "static"
    if not url:
        return None
    return parse_proxy_line(url, mode=mode if mode in ("static", "sticky") else "static")


def _remember(user_id: int, proxy: ParsedProxy) -> ParsedProxy:
    db.set_user_proxy(user_id, vault.encrypt_password(proxy.as_url()), proxy.label, proxy.mode)
    return proxy


def _refresh_password(proxy: ParsedProxy, static: list[ParsedProxy]) -> ParsedProxy:
    for item in static:
        if item.identity == proxy.identity:
            return ParsedProxy(item.host, item.port, item.username, item.password, proxy.mode)
    return proxy


def assign_proxy(user_id: int) -> ParsedProxy | None:
    """Return this client's pinned exit, assigning one on first use.

    None means no residential proxy is configured and the server IP is used.
    """
    static, template = load_pool()
    current = _stored_proxy(user_id)
    if current is not None:
        if current.mode != "sticky":
            refreshed = _refresh_password(current, static)
            if refreshed.as_url() != current.as_url():
                return _remember(user_id, refreshed)
        return current
    if not static and not template:
        return None

    taken_slots: set[tuple[str, int, str]] = set()
    taken_credentials: set[tuple[str, int, str, str]] = set()
    for row in db.list_proxy_secrets():
        cipher = row.get("proxy_ciphertext") or ""
        if not cipher or int(row["id"]) == int(user_id):
            continue
        try:
            url = vault.decrypt_password(cipher)
        except ValueError:
            continue
        if not url:
            continue
        try:
            other = parse_proxy_line(url, mode=row.get("proxy_mode") or "static")
        except EgressError:
            continue
        taken_slots.add(other.identity)
        taken_credentials.add(other.credential)

    for item in static:
        if item.identity not in taken_slots:
            return _remember(user_id, item)

    if template:
        for _ in range(5):
            rendered = template.replace("{session}", secrets.token_hex(6))
            proxy = parse_proxy_line(rendered, mode="sticky")
            if proxy.credential not in taken_credentials:
                return _remember(user_id, proxy)
        raise EgressError("Could not pick a free sticky session for this client.")

    raise EgressError(
        "No free residential proxy for this client. Add another exit, or release one that is no longer used."
    )


def pool_summary() -> dict:
    static, template = load_pool()
    assigned = []
    used_static: set[tuple[str, int, str]] = set()
    for row in db.list_proxy_secrets():
        if not (row.get("proxy_label") or row.get("proxy_ciphertext")):
            continue
        assigned.append(
            {
                "id": row["id"],
                "email": row.get("email") or "",
                "label": row.get("proxy_label") or "",
                "mode": row.get("proxy_mode") or "",
            }
        )
        if (row.get("proxy_mode") or "static") == "sticky":
            continue
        cipher = row.get("proxy_ciphertext") or ""
        if not cipher:
            continue
        try:
            used_static.add(parse_proxy_line(vault.decrypt_password(cipher)).identity)
        except (EgressError, ValueError):
            continue
    free_count = sum(1 for item in static if item.identity not in used_static)
    masked_template = ""
    if template:
        try:
            parsed = parse_proxy_line(template.replace("{session}", "sessiontoken"), mode="sticky")
            user = parsed.username.replace("sessiontoken", "{session}")
            if user or parsed.password:
                masked_template = f"http://{user}:***@{parsed.host}:{parsed.port}"
            else:
                masked_template = parsed.masked()
        except EgressError:
            masked_template = ""
    return {
        "configured": bool(static or template),
        "static_count": len(static),
        "free_count": free_count,
        "static_masked": [item.masked() for item in static],
        "template": bool(template),
        "template_masked": masked_template,
        "assigned": assigned,
    }


class LocalForwarder:
    """HTTP proxy on 127.0.0.1 that injects upstream proxy authentication."""

    def __init__(self, upstream: ParsedProxy):
        self.upstream = upstream
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = int(self._sock.getsockname()[1])
        self._sock.listen(64)
        self._sock.settimeout(0.5)
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._thread = threading.Thread(target=self._accept_loop, name="sendline-proxy", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream_sock: socket.socket | None = None
        try:
            client.settimeout(30)
            head, rest = _read_headers(client)
            if not head:
                return
            first = head.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
            parts = first.split()
            if len(parts) < 2:
                _send_status(client, 400, "Bad Request")
                return
            method, target = parts[0].upper(), parts[1]
            upstream_sock = socket.create_connection((self.upstream.host, self.upstream.port), timeout=30)
            upstream_sock.settimeout(30)
            if method == "CONNECT":
                host_port = target
                _send_connect(upstream_sock, self.upstream, host_port)
                status, leftover = _read_status(upstream_sock)
                if status != 200:
                    _send_status(client, 502, "Bad Gateway")
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if leftover:
                    client.sendall(leftover)
                if rest:
                    upstream_sock.sendall(rest)
                client.settimeout(None)
                upstream_sock.settimeout(None)
                _relay(client, upstream_sock)
                return
            _forward_http(upstream_sock, self.upstream, head, rest)
            client.settimeout(None)
            upstream_sock.settimeout(None)
            _relay(client, upstream_sock)
        except OSError:
            try:
                _send_status(client, 502, "Bad Gateway")
            except OSError:
                pass
        finally:
            for sock in (client, upstream_sock):
                if sock is None:
                    continue
                try:
                    sock.close()
                except OSError:
                    pass


def _read_headers(sock: socket.socket, limit: int = 65536) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            break
    head, sep, rest = data.partition(b"\r\n\r\n")
    if not sep:
        return b"", b""
    return head, rest


def _auth_header(proxy: ParsedProxy) -> str:
    if not proxy.username and not proxy.password:
        return ""
    token = base64.b64encode(f"{proxy.username}:{proxy.password}".encode("utf-8")).decode("ascii")
    return f"Proxy-Authorization: Basic {token}"


def _send_connect(sock: socket.socket, proxy: ParsedProxy, host_port: str) -> None:
    lines = [
        f"CONNECT {host_port} HTTP/1.1",
        f"Host: {host_port}",
    ]
    auth = _auth_header(proxy)
    if auth:
        lines.append(auth)
    lines.append("")
    lines.append("")
    sock.sendall("\r\n".join(lines).encode("ascii", "replace"))


def _read_status(sock: socket.socket) -> tuple[int, bytes]:
    head, rest = _read_headers(sock)
    if not head:
        return 0, b""
    first = head.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
    parts = first.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return 0, rest
    return int(parts[1]), rest


def _send_status(sock: socket.socket, code: int, reason: str) -> None:
    body = reason.encode("ascii", "replace")
    sock.sendall(
        f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
        + body
    )


def _forward_http(sock: socket.socket, proxy: ParsedProxy, head: bytes, rest: bytes) -> None:
    text = head.decode("iso-8859-1", "replace")
    lines = text.split("\r\n")
    kept = [lines[0]]
    for line in lines[1:]:
        if line.lower().startswith("proxy-authorization:"):
            continue
        kept.append(line)
    auth = _auth_header(proxy)
    if auth:
        kept.append(auth)
    payload = ("\r\n".join(kept) + "\r\n\r\n").encode("iso-8859-1", "replace") + rest
    sock.sendall(payload)


def _relay(left: socket.socket, right: socket.socket) -> None:
    pair = [left, right]
    try:
        while True:
            readable, _, _ = select.select(pair, [], [], 60)
            if not readable:
                break
            for src in readable:
                dst = right if src is left else left
                try:
                    data = src.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        return


def connect_status(local_port: int, host: str, port: int, timeout: float = 20) -> int:
    sock = socket.create_connection(("127.0.0.1", local_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii"))
        status, _rest = _read_status(sock)
        return status
    finally:
        sock.close()


def lookup_public_ip(local_port: int, timeout: float = 15) -> str:
    handler = ProxyHandler(
        {
            "http": f"http://127.0.0.1:{local_port}",
            "https": f"http://127.0.0.1:{local_port}",
        }
    )
    opener = build_opener(handler)
    req = Request("https://api.ipify.org", headers={"User-Agent": "sendline-egress-check"})
    with opener.open(req, timeout=timeout) as resp:
        text = resp.read(64).decode("ascii", "replace").strip()
    if not text or any(c not in "0123456789.:" for c in text):
        return ""
    return text


def probe(local_port: int) -> str:
    """Confirm the upstream accepts a tunnel. Return the public IP when it can be read."""
    status = connect_status(local_port, "www.linkedin.com", 443)
    if status == 407:
        raise EgressError("Residential proxy rejected the username or password.")
    if status != 200:
        raise EgressError(f"Residential proxy did not open a tunnel (HTTP {status or 'no response'}).")
    try:
        return lookup_public_ip(local_port)
    except Exception:
        return ""


def _self_test() -> None:
    static, template = parse_pool_text(
        "\n".join(
            [
                "# comment",
                "203.0.113.10:8000:alice:secret",
                "http://bob:p%40ss@203.0.113.11:8001",
                "http://user-session-{session}:pw@gate.example.com:7000",
            ]
        )
    )
    assert len(static) == 2
    assert static[0].username == "alice" and static[0].password == "secret"
    assert static[1].password == "p@ss"
    assert "{session}" in template

    class FakeUpstream(threading.Thread):
        def __init__(self):
            super().__init__(daemon=True)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("127.0.0.1", 0))
            self.port = int(self.sock.getsockname()[1])
            self.sock.listen(8)
            self.ok = threading.Event()
            self.auth = ""

        def run(self):
            conn, _ = self.sock.accept()
            head, rest = _read_headers(conn)
            text = head.decode("iso-8859-1", "replace")
            for line in text.split("\r\n"):
                if line.lower().startswith("proxy-authorization:"):
                    self.auth = line.split(":", 1)[1].strip()
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                conn.sendall(rest)
            data = b""
            conn.settimeout(2)
            try:
                while len(data) < 4:
                    chunk = conn.recv(16)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass
            conn.sendall(b"pong" if data.startswith(b"ping") else b"nope")
            self.ok.set()
            conn.close()
            self.sock.close()

    fake = FakeUpstream()
    fake.start()
    proxy = ParsedProxy("127.0.0.1", fake.port, "alice", "secret", "static")
    forwarder = LocalForwarder(proxy)
    forwarder.start()
    try:
        client = socket.create_connection(("127.0.0.1", forwarder.port), timeout=5)
        client.sendall(b"CONNECT www.linkedin.com:443 HTTP/1.1\r\nHost: www.linkedin.com:443\r\n\r\n")
        status, _rest = _read_status(client)
        assert status == 200, status
        client.sendall(b"ping")
        echoed = client.recv(16)
        client.close()
        fake.ok.wait(3)
        expected = "Basic " + base64.b64encode(b"alice:secret").decode("ascii")
        assert fake.auth == expected, fake.auth
        assert echoed == b"pong", echoed
    finally:
        forwarder.stop()
    print("egress self-test ok")


if __name__ == "__main__":
    _self_test()
