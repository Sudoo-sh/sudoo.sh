"""A tiny S3-compatible server for the tests: path-style addressing, objects kept in memory, the calls offsite/s3.py
makes (PUT, GET, HEAD, DELETE, ListObjectsV2, multipart uploads). Like S3, it refuses a request that is not signed
for its access key, and a body whose SHA-256 differs from the signed x-amz-content-sha256. It never contacts
anything."""

from __future__ import annotations

import hashlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape

ACCESS_KEY = "AKIATESTOFFSITE0001"
SECRET_KEY = "test-secret-not-real-0123456789abcdef"
BUCKET = "sudoo-test"


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.requests: list[tuple[str, str]] = []
        self.refuse: str | None = None  # an S3 error code every request gets, to test failures
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _answer(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _error(self, status: int, code: str) -> None:
                body = f"<Error><Code>{code}</Code><Message>{code}</Message></Error>".encode()
                self._answer(status, body, {"Content-Type": "application/xml"})

            def _handle(self) -> None:
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
                path = unquote(parts.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                fake.requests.append((self.command, path))
                auth = self.headers.get("Authorization", "")
                if not auth.startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS_KEY}/"):
                    return self._error(403, "InvalidAccessKeyId")
                if fake.refuse:
                    return self._error(403, fake.refuse)
                if hashlib.sha256(body).hexdigest() != self.headers.get("x-amz-content-sha256"):
                    return self._error(400, "XAmzContentSHA256Mismatch")
                bucket, _, key = path.lstrip("/").partition("/")
                if bucket != BUCKET:
                    return self._error(404, "NoSuchBucket")
                if not key and self.command == "GET":
                    return self._list(query)
                if self.command == "PUT" and "uploadId" in query:
                    fake.uploads[query["uploadId"]][int(query["partNumber"])] = body
                    return self._answer(200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})  # noqa: S324
                if self.command == "PUT":
                    fake.objects[key] = body
                    return self._answer(200, headers={"ETag": '"x"'})
                if self.command == "POST" and "uploads" in query:
                    upload_id = f"up{len(fake.uploads) + 1}"
                    fake.uploads[upload_id] = {}
                    xml = f"<InitiateMultipartUploadResult><UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>"
                    return self._answer(200, xml.encode())
                if self.command == "POST" and "uploadId" in query:
                    parts_sent = fake.uploads.pop(query["uploadId"])
                    numbers = [int(n) for n in re.findall(rb"<PartNumber>(\d+)</PartNumber>", body)]
                    fake.objects[key] = b"".join(parts_sent[n] for n in numbers)
                    return self._answer(200, b"<CompleteMultipartUploadResult/>")
                if self.command == "DELETE":
                    if "uploadId" in query:
                        fake.uploads.pop(query["uploadId"], None)
                    else:
                        fake.objects.pop(key, None)
                    return self._answer(204)
                if key not in fake.objects:
                    return self._error(404, "NoSuchKey")
                data = fake.objects[key]
                return self._answer(200, data, {"Content-Type": "application/octet-stream"})

            def _list(self, query: dict[str, str]) -> None:
                prefix = query.get("prefix", "")
                found = sorted(k for k in fake.objects if k.startswith(prefix))
                start = int(query.get("continuation-token") or 0)
                page, more = found[start : start + 2], start + 2 < len(found)  # small pages: paging is tested too
                items = "".join(
                    f"<Contents><Key>{escape(k)}</Key><Size>{len(fake.objects[k])}</Size>"
                    "<LastModified>2026-11-18T00:00:00.000Z</LastModified></Contents>"
                    for k in page
                )
                tail = f"<NextContinuationToken>{start + 2}</NextContinuationToken>" if more else ""
                xml = (
                    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{items}{tail}</ListBucketResult>"
                )
                self._answer(200, xml.encode(), {"Content-Type": "application/xml"})

            do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
