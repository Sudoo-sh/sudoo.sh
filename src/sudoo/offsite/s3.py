"""A small client for S3-compatible storage (Cloudflare R2, AWS S3, Backblaze B2, MinIO...): the few calls off-site
copies need, made with the standard library and AWS Signature Version 4.

Why not boto3: it and botocore add about 100 MB and many releases a year to the panel for six calls. These calls are
plain HTTPS requests; signing them is the documented SigV4 algorithm (tested against AWS's published example in
tests/test_offsite.py).

    put_file      upload a file (one request up to PART_SIZE, a multipart upload above it); the SHA-256 of every
                  request body is signed, so the storage refuses a body changed on the way
    put_bytes     upload a small object (manifest, signature, connection test)
    get_to_file   download an object into a file, piece by piece
    get_bytes     download a small object
    head          size of an object, or None when it does not exist
    delete        remove an object (no error when it does not exist)
    list          every object under a prefix (ListObjectsV2, page after page)

Every error is an S3Error whose text says what went wrong in plain words, never containing the secret.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import http.client
import ssl
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote, urlsplit

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
PART_SIZE = 64 * 1024 * 1024  # parts of a multipart upload; S3 allows 10 000 parts, so files up to 625 GB
_CHUNK = 1024 * 1024
_MAX_ANSWER = 16 * 1024 * 1024  # an XML answer larger than this is not one the storage sends
_RETRIES = 3


class S3Error(Exception):
    """A request failed; the text says why. `status` is the HTTP status (0: the storage could not be reached)."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Destination:
    endpoint: str  # https://host[:port]
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    addressing: str = "path"  # "path" or "virtual"
    timeout: float = 60


# ---------------------------------------------------------------- signing (AWS Signature Version 4)


def _uri_encode(value: str, safe: str = "") -> str:
    return quote(value, safe="-_.~" + safe)


def sign(
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_sha256: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """The headers of a signed request: `headers` with host, x-amz-date, x-amz-content-sha256 and Authorization.

    `path` is not encoded yet (each segment is encoded here, "/" kept); `query` neither."""
    when = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    day = when[:8]
    signed = {k.lower(): " ".join(str(v).strip().split()) for k, v in headers.items()}
    signed |= {"host": host, "x-amz-date": when, "x-amz-content-sha256": payload_sha256}
    names = sorted(signed)
    canonical = "\n".join(
        [
            method,
            _uri_encode(path, "/"),
            "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items())),
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        ]
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{when}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = f"AWS4{secret_access_key}".encode()
    for part in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    signed["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return signed


# ---------------------------------------------------------------- bodies


class _Slice:
    """A part of a file, read like a file (for a request body)."""

    def __init__(self, path: Path, offset: int, length: int) -> None:
        self._file = open(path, "rb")  # noqa: SIM115 - closed by close(), when the request is sent
        self._file.seek(offset)
        self._left = length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        size = self._left if size < 0 else min(size, self._left)
        data = self._file.read(size)
        self._left -= len(data)
        return data

    def close(self) -> None:
        self._file.close()


def sha256_of(path: Path, offset: int = 0, length: int | None = None) -> str:
    """The SHA-256 (hex) of a file, or of `length` bytes of it from `offset`."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        source.seek(offset)
        left = length
        while left is None or left > 0:
            chunk = source.read(_CHUNK if left is None else min(_CHUNK, left))
            if not chunk:
                break
            digest.update(chunk)
            if left is not None:
                left -= len(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- the client


class Client:
    def __init__(self, destination: Destination) -> None:
        self.d = destination
        parts = urlsplit(destination.endpoint)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise S3Error("The storage address is not an https:// address.")
        self._scheme = parts.scheme
        self._port = parts.port
        if destination.addressing == "virtual":
            self._host = f"{destination.bucket}.{parts.hostname}"
            self._base = ""
        else:
            self._host = parts.hostname
            self._base = f"/{destination.bucket}"
        self._host_header = self._host + (f":{parts.port}" if parts.port else "")

    # -------------------------------------------------------- one request

    def _connection(self) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=self.d.timeout, context=ssl.create_default_context(), blocksize=_CHUNK
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=self.d.timeout, blocksize=_CHUNK)

    def _request(
        self,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: Callable[[], Any] | None = None,  # a file-like object or bytes
        length: int = 0,
        payload_sha256: str = EMPTY_SHA256,
        into: IO[bytes] | None = None,
        ok: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, str], bytes]:
        """Send one signed request, again (up to _RETRIES) when the storage could not be reached or answered 500 or
        503; `body()` gives the body anew for each attempt. With `into`, a 200 answer's body is written there and
        not returned. Returns (status, headers, body)."""
        query = query or {}
        path = self._base + "/" + key if key else (self._base or "/")
        target = _uri_encode(path, "/") + (
            "?" + "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items())) if query else ""
        )
        last: S3Error | None = None
        for attempt in range(_RETRIES):
            if attempt:
                time.sleep(2**attempt)
            signed = sign(
                method, self._host_header, path, query, dict(headers or {}), payload_sha256,
                self.d.access_key_id, self.d.secret_access_key, self.d.region,
            )  # fmt: skip
            if body is not None or method in ("PUT", "POST"):
                signed["content-length"] = str(length)
            connection = self._connection()
            data = body() if body is not None else None
            try:
                connection.request(method, target, body=data, headers=signed)
                answer = connection.getresponse()
                answer_headers = {k.lower(): v for k, v in answer.getheaders()}
                if answer.status in ok and into is not None and answer.status == 200:
                    while chunk := answer.read(_CHUNK):
                        into.write(chunk)
                    return answer.status, answer_headers, b""
                content = answer.read(_MAX_ANSWER)
            except (OSError, http.client.HTTPException) as error:
                last = S3Error(f"The storage could not be reached ({self._host}): {error}")
                if into is not None:
                    into.seek(0)
                    into.truncate()
                continue
            finally:
                if data is not None and hasattr(data, "close"):
                    data.close()
                connection.close()
            if answer.status in ok:
                return answer.status, answer_headers, content
            last = _error(answer.status, content, method, key, self._host)
            if answer.status not in (500, 502, 503, 504):
                break
        assert last is not None
        raise last

    # -------------------------------------------------------- objects

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._request(
            "PUT", key, headers={"content-type": content_type}, body=lambda: data, length=len(data),
            payload_sha256=hashlib.sha256(data).hexdigest(),
        )  # fmt: skip

    def put_file(
        self, key: str, path: Path, sha256: str | None = None, log: Callable[[str], None] | None = None
    ) -> None:
        """Upload `path` as `key`. `sha256`: the file's, when known (a single request signs it)."""
        size = path.stat().st_size
        if size <= PART_SIZE:
            digest = sha256 or sha256_of(path)
            self._request(
                "PUT", key, headers={"content-type": "application/zip"}, body=lambda: open(path, "rb"),  # noqa: SIM115 - closed after the request
                length=size, payload_sha256=digest,
            )  # fmt: skip
            return
        _, _, answer = self._request("POST", key, query={"uploads": ""}, headers={"content-type": "application/zip"})
        upload_id = _text(answer, "UploadId")
        if not upload_id:
            raise S3Error("The storage did not start the upload (no UploadId in its answer).")
        etags: list[str] = []
        try:
            for number, offset in enumerate(range(0, size, PART_SIZE), start=1):
                length = min(PART_SIZE, size - offset)
                digest = sha256_of(path, offset, length)
                _, headers, _ = self._request(
                    "PUT", key, query={"partNumber": str(number), "uploadId": upload_id},
                    body=functools.partial(_Slice, path, offset, length),
                    length=length, payload_sha256=digest,
                )  # fmt: skip
                etags.append(headers.get("etag", ""))
                if log:
                    log(f"  part {number} of {-(-size // PART_SIZE)} sent\n")
            parts = "".join(
                f"<Part><PartNumber>{n}</PartNumber><ETag>{_xml_escape(tag)}</ETag></Part>"
                for n, tag in enumerate(etags, start=1)
            )
            document = f"<CompleteMultipartUpload>{parts}</CompleteMultipartUpload>".encode()
            _, _, answer = self._request(
                "POST", key, query={"uploadId": upload_id}, headers={"content-type": "application/xml"},
                body=lambda: document, length=len(document), payload_sha256=hashlib.sha256(document).hexdigest(),
            )  # fmt: skip
            if b"<Error>" in answer:  # S3 may answer 200 and put the error in the body
                raise _error(200, answer, "POST", key, self._host)
        except BaseException:
            with contextlib.suppress(S3Error):  # else the bucket's lifecycle rules remove unfinished uploads
                self._request("DELETE", key, query={"uploadId": upload_id}, ok=(200, 204, 404))
            raise

    def get_to_file(self, key: str, target: IO[bytes]) -> None:
        self._request("GET", key, into=target)

    def get_bytes(self, key: str, limit: int = _MAX_ANSWER) -> bytes:
        _, _, answer = self._request("GET", key)
        if len(answer) >= limit:
            raise S3Error(f"{key} is larger than expected.")
        return answer

    def head(self, key: str) -> int | None:
        status, headers, _ = self._request("HEAD", key, ok=(200, 404))
        return int(headers.get("content-length", "0")) if status == 200 else None

    def delete(self, key: str) -> None:
        self._request("DELETE", key, ok=(200, 204, 404))

    def list(self, prefix: str) -> Iterator[dict[str, Any]]:
        """Every object whose key starts with `prefix`: {"key", "size", "modified"} (a timestamp)."""
        token = ""
        while True:
            query = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            _, _, answer = self._request("GET", "", query=query)
            root = _parse(answer)
            for item in root.iter():
                if _local(item.tag) != "Contents":
                    continue
                values = {_local(child.tag): (child.text or "") for child in item}
                yield {
                    "key": values.get("Key", ""),
                    "size": int(values.get("Size") or 0),
                    "modified": _timestamp(values.get("LastModified", "")),
                }
            truncated = next((e.text for e in root if _local(e.tag) == "IsTruncated"), "false")
            token = next((e.text or "" for e in root if _local(e.tag) == "NextContinuationToken"), "")
            if truncated != "true" or not token:
                return


# ---------------------------------------------------------------- answers


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(answer: bytes) -> ET.Element:
    try:
        return ET.fromstring(answer)  # noqa: S314 - the configured storage's answer, size-limited (_MAX_ANSWER)
    except ET.ParseError as error:
        raise S3Error(f"The storage gave an answer that is not S3 XML: {error}") from None


def _text(answer: bytes, name: str) -> str:
    return next((e.text or "" for e in _parse(answer).iter() if _local(e.tag) == name), "")


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_PLAIN = {
    "AccessDenied": "the access key may not do this in this bucket",
    "InvalidAccessKeyId": "the access key ID is not known to the storage",
    "SignatureDoesNotMatch": "the secret access key does not match the access key ID (or the region is wrong)",
    "NoSuchBucket": "the bucket does not exist",
    "AuthorizationHeaderMalformed": "the region is wrong for this bucket",
    "PermanentRedirect": "the bucket is in another region, or needs the other addressing style",
    "XAmzContentSHA256Mismatch": "the data changed on its way to the storage",
    "BadDigest": "the data changed on its way to the storage",
    "NoSuchKey": "the file is not in the bucket",
    "RequestTimeTooSkewed": "this server's clock is wrong",
}


def _error(status: int, content: bytes, method: str, key: str, host: str) -> S3Error:
    code = message = ""
    if content.lstrip().startswith(b"<"):
        try:
            root = ET.fromstring(content)  # noqa: S314 - see _parse
            code = next((e.text or "" for e in root.iter() if _local(e.tag) == "Code"), "")
            message = next((e.text or "" for e in root.iter() if _local(e.tag) == "Message"), "")
        except ET.ParseError:
            pass
    if not code and status == 404:
        code = "NoSuchKey" if key else "NoSuchBucket"
    reason = _PLAIN.get(code) or message or f"HTTP {status}"
    what = f"{method} {key or '(the bucket)'}"
    return S3Error(f"The storage refused {what}: {reason}" + (f" ({code})" if code else "") + ".", status, code)
