"""Fixed-origin, bounded Cloudflare read transport; no provider bodies in errors."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class DnsConnectionError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def hostname(value, *, record=False):
    if not isinstance(value, str) or not value or len(value) > 255 or value != value.strip():
        raise DnsConnectionError("INVALID_HOSTNAME")
    raw = value[:-1] if value.endswith(".") else value
    try:
        normalized = raw.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise DnsConnectionError("INVALID_HOSTNAME") from None
    labels = normalized.split(".")
    pattern = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if record:
        pattern = r"(?:\*|[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)"
    if len(normalized) > 253 or len(labels) < 2 or any(not re.fullmatch(pattern, x) for x in labels):
        raise DnsConnectionError("INVALID_HOSTNAME")
    if "*" in labels[1:]:
        raise DnsConnectionError("INVALID_HOSTNAME")
    return normalized


def native_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise DnsConnectionError("INVALID_PROVIDER_ID")
    return value


def token_text(value):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 4096
        or not re.fullmatch(r"[A-Za-z0-9._~-]+", value)
    ):
        raise DnsConnectionError("INVALID_TOKEN")
    return value


class CloudflareReadClient:
    """One finite operation. Each native response triggers current admission again."""

    def __init__(self, token, checkpoint, *, opener=None, clock=time.monotonic):
        from django.db import connection

        if connection.in_atomic_block:
            raise DnsConnectionError("TRANSACTION_UNSUPPORTED")
        self._token = token_text(token) if token is not None else None
        self._checkpoint = checkpoint
        self._clock = clock
        self._deadline = clock() + 30
        self._bytes = 0
        self._requests = 0
        checkpoint()
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.current()

    def current(self):
        self._checkpoint()
        if self._clock() >= self._deadline:
            raise DnsConnectionError("DEADLINE_EXCEEDED")

    def json(self, path, *, form=None):
        self.current()
        if self._requests >= 12:
            raise DnsConnectionError("INVENTORY_LIMIT")
        if form is None:
            if self._token is None:
                raise DnsConnectionError("CREDENTIAL_UNAVAILABLE")
            if not re.fullmatch(
                r"/zones(?:/[0-9a-f]{32}(?:/dns_records)?)?(?:\?page=[1-8]&per_page=50)?", path
            ):
                raise DnsConnectionError("INVALID_NATIVE_PATH")
            url = "https://api.cloudflare.com/client/v4" + path
            data = None
            headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        else:
            if path not in ("/oauth2/token", "/oauth2/revoke"):
                raise DnsConnectionError("INVALID_NATIVE_PATH")
            url = "https://dash.cloudflare.com" + path
            data = urllib.parse.urlencode(form).encode()
            headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        request = urllib.request.Request(url, data=data, headers=headers)
        self._requests += 1
        try:
            with self._opener.open(request, timeout=min(10, self._deadline - self._clock())) as response:
                self.current()
                if response.status != 200:
                    raise DnsConnectionError("UPSTREAM_UNAVAILABLE")
                chunks = []
                size = 0
                while size <= 524288:
                    self.current()
                    if response.fp is None:
                        break
                    # A read1 is one bounded socket read, not a whole-body wait.
                    response.fp.raw._sock.settimeout(min(10, self._deadline - self._clock()))
                    chunk = response.read1(min(65536, 524289 - size))
                    self.current()
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                body = b"".join(chunks)
                self.current()
            self._bytes += len(body)
            if len(body) > 524288 or self._bytes > 2097152:
                raise DnsConnectionError("RESPONSE_LIMIT")
            # RFC 7009 revocation success is status-only, including an empty body.
            if form is not None and path == "/oauth2/revoke":
                return {}
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise DnsConnectionError("INVALID_RESPONSE")
            return payload
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            self.current()
            raise DnsConnectionError(
                {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 429: "RATE_LIMITED"}.get(
                    code, "UPSTREAM_UNAVAILABLE"
                )
            ) from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, DnsConnectionError):
                raise
            self.current()
            raise DnsConnectionError("UPSTREAM_UNAVAILABLE") from None

    def _result(self, payload):
        if payload.get("success") is not True or payload.get("errors") not in (None, []):
            raise DnsConnectionError("INVALID_RESPONSE")
        return payload.get("result")

    def inventory(self, path, convert):
        output = []
        seen = set()
        expected_total = None
        for page in range(1, 9):
            payload = self.json(f"{path}?page={page}&per_page=50")
            rows = self._result(payload)
            meta = payload.get("result_info")
            if not isinstance(rows, list) or len(rows) > 50 or not isinstance(meta, dict):
                raise DnsConnectionError("INVALID_PAGINATION")
            count, total, pages = meta.get("count"), meta.get("total_count"), meta.get("total_pages")
            if (
                any(type(x) is not int or x < 0 for x in (count, total, pages))
                or type(meta.get("page")) is not int
                or type(meta.get("per_page")) is not int
                or meta.get("page") != page
                or meta.get("per_page") != 50
                or count != len(rows)
            ):
                raise DnsConnectionError("INVALID_PAGINATION")
            if total > 400 or pages > 8:
                raise DnsConnectionError("INVENTORY_LIMIT")
            if expected_total is not None and expected_total != (total, pages):
                raise DnsConnectionError("INVENTORY_CHANGED")
            expected_total = (total, pages)
            for row in rows:
                item = convert(row)
                if item["id"] in seen:
                    raise DnsConnectionError("INVALID_PAGINATION")
                seen.add(item["id"])
                output.append(item)
            if page >= max(pages, 1):
                if len(output) != total:
                    raise DnsConnectionError("INVALID_PAGINATION")
                self.current()
                return output
            if not rows:
                raise DnsConnectionError("INVALID_PAGINATION")
        raise DnsConnectionError("INVENTORY_LIMIT")

    def zones(self):
        return self.inventory("/zones", zone_metadata)

    def zone(self, zone_id, zone_name):
        item = zone_metadata(self._result(self.json("/zones/" + native_id(zone_id))))
        if item["id"] != zone_id or item["name"] != hostname(zone_name):
            raise DnsConnectionError("ZONE_CHANGED")
        return item

    def records(self, zone_id, zone_name):
        before = self.zone(zone_id, zone_name)
        result = self.inventory(
            "/zones/" + native_id(zone_id) + "/dns_records", lambda row: record_metadata(row, before["name"])
        )
        if before != self.zone(zone_id, zone_name):
            raise DnsConnectionError("ZONE_CHANGED")
        return result


def zone_metadata(row):
    if not isinstance(row, dict) or not isinstance(row.get("account"), dict):
        raise DnsConnectionError("INVALID_RESPONSE")
    servers = row.get("name_servers")
    if not isinstance(servers, list) or len(servers) > 16:
        raise DnsConnectionError("INVALID_RESPONSE")
    state = row.get("status")
    if state not in ("initializing", "pending", "active", "moved", "deleted", "deactivated"):
        raise DnsConnectionError("INVALID_RESPONSE")
    return {
        "id": native_id(row.get("id")),
        "name": hostname(row.get("name")),
        "account_id": native_id(row["account"].get("id")),
        "status": state,
        "name_servers": [hostname(x) for x in servers],
    }


def record_metadata(row, zone):
    if not isinstance(row, dict):
        raise DnsConnectionError("INVALID_RESPONSE")
    name = hostname(row.get("name"), record=True)
    if name != zone and not name.endswith("." + zone):
        raise DnsConnectionError("RECORD_OUTSIDE_ZONE")
    kind, content, ttl, proxied = (
        row.get("type"),
        row.get("content"),
        row.get("ttl"),
        row.get("proxied", False),
    )
    if (
        kind
        not in (
            "A",
            "AAAA",
            "CAA",
            "CERT",
            "CNAME",
            "DNSKEY",
            "DS",
            "HTTPS",
            "LOC",
            "MX",
            "NAPTR",
            "NS",
            "OPENPGPKEY",
            "PTR",
            "SMIMEA",
            "SRV",
            "SSHFP",
            "SVCB",
            "TLSA",
            "TXT",
            "URI",
        )
        or not isinstance(content, str)
        or len(content.encode()) > 4096
        or type(ttl) is not int
        or not 1 <= ttl <= 86400
        or type(proxied) is not bool
    ):
        raise DnsConnectionError("INVALID_RECORD")
    priority = row.get("priority")
    if priority is not None and (type(priority) is not int or not 0 <= priority <= 65535):
        raise DnsConnectionError("INVALID_RECORD")
    return {
        "id": native_id(row.get("id")),
        "name": name,
        "type": kind,
        "content": content,
        "ttl": ttl,
        "proxied": proxied,
        "priority": priority,
    }
