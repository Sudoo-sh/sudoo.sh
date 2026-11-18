"""A small ACME client (RFC 8555): certificates from Let's Encrypt or another ACME authority, by HTTP-01 challenge.

Why a client of our own: the well-known Python ACME library (certbot's `acme`) brings pyOpenSSL, josepy, requests
and more into the panel; what the panel needs is a few signed JSON requests, which `cryptography` (already a
dependency) does. This module implements exactly that much of RFC 8555:

    account      newAccount with an ES256 (P-256) key, contact e-mail, terms of service agreed (the administrator
                 agrees by choosing CUSTOM_DOMAINS=acme; docs/ADMIN_GUIDE.md says so); the account URL is kept
    order        newOrder for one domain -> its authorization -> the http-01 challenge -> the panel writes the key
                 authorization where the gateway serves it (/.well-known/acme-challenge/<token>) -> the authority
                 checks it over http -> finalize with a CSR (a new P-256 key for each certificate) -> the
                 certificate chain in PEM
    nonces       every request carries a fresh Replay-Nonce; one "badNonce" answer is retried with a new one

Requests go through a Transport (urllib by default) so tests run against a fake authority and never contact a real
one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

DIRECTORIES = {
    "production": "https://acme-v02.api.letsencrypt.org/directory",
    "staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}
USER_AGENT = "sudoo-panel-acme/1"
POLL_SECONDS = 2.0
POLL_LIMIT_SECONDS = 120.0


class AcmeError(Exception):
    """The authority refused or failed; the message is meant for people (from the authority's problem detail)."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind  # the problem type without its urn prefix, such as "rateLimited" or "unauthorized"


@dataclass(frozen=True, slots=True)
class Answer:
    status: int
    headers: dict[str, str]  # lower-case names
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class Transport(Protocol):
    def __call__(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> Answer: ...


def urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> Answer:
    """HTTPS requests with the standard library (certificate checks on), 30 seconds each."""
    if not url.startswith("https://") and not url.startswith(("http://localhost", "http://127.0.0.1")):
        raise AcmeError(f"Refusing an ACME address that is not https: {url[:100]}")
    request = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https only, checked above
            return Answer(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read())
    except urllib.error.HTTPError as error:
        return Answer(error.code, {k.lower(): v for k, v in error.headers.items()}, error.read())
    except (OSError, TimeoutError) as error:
        raise AcmeError(f"The ACME authority could not be reached ({error.__class__.__name__}).") from error


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def load_key(pem: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("not an EC key")
    return key


def public_jwk(public: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = public.public_numbers()
    return {
        "crv": "P-256",
        "kty": "EC",
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
    }


def jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    return public_jwk(key.public_key())


def thumbprint_of(public: ec.EllipticCurvePublicKey) -> str:
    """RFC 7638 JWK thumbprint (the second half of every key authorization)."""
    canonical = json.dumps(public_jwk(public), sort_keys=True, separators=(",", ":")).encode()
    return b64(hashlib.sha256(canonical).digest())


def thumbprint(key: ec.EllipticCurvePrivateKey) -> str:
    return thumbprint_of(key.public_key())


def sign(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """ES256: the raw r || s signature JOSE expects (not DER)."""
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def csr(domain: str, key: ec.EllipticCurvePrivateKey) -> bytes:
    """A certificate request for `domain` (DER)."""
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return request.public_bytes(serialization.Encoding.DER)


def directory_url(choice: str) -> str:
    return DIRECTORIES.get(choice, choice)


def _problem(answer: Answer, what: str) -> AcmeError:
    data = answer.json()
    kind = str(data.get("type", "")).rsplit(":", 1)[-1] if isinstance(data, dict) else ""
    detail = str(data.get("detail", "")) if isinstance(data, dict) else ""
    return AcmeError(f"{what}: {detail or f'HTTP {answer.status}'}"[:500], kind)


class Client:
    """One ACME account at one authority. Not thread-safe: the panel uses one at a time (domains/worker.py)."""

    def __init__(
        self,
        directory: str,
        account_key: ec.EllipticCurvePrivateKey,
        account_url: str | None = None,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.directory_url = directory
        self.key = account_key
        self.account_url = account_url
        self.transport = transport
        self.sleep = sleep
        self._directory: dict[str, Any] | None = None
        self._nonce: str | None = None

    # ---- plumbing

    def directory(self) -> dict[str, Any]:
        if self._directory is None:
            answer = self.transport("GET", self.directory_url, None, {})
            data = answer.json()
            if answer.status != 200 or not isinstance(data, dict) or "newOrder" not in data:
                raise AcmeError(f"{self.directory_url} is not an ACME directory (HTTP {answer.status}).")
            self._directory = data
        return self._directory

    def _new_nonce(self) -> str:
        answer = self.transport("HEAD", str(self.directory()["newNonce"]), None, {})
        nonce = answer.headers.get("replay-nonce")
        if not nonce:
            raise AcmeError("The ACME authority gave no nonce.")
        return nonce

    def _signed(self, url: str, payload: Any, nonce: str, use_jwk: bool) -> bytes:
        protected: dict[str, Any] = {"alg": "ES256", "nonce": nonce, "url": url}
        if use_jwk or not self.account_url:
            protected["jwk"] = jwk(self.key)
        else:
            protected["kid"] = self.account_url
        encoded_protected = b64(json.dumps(protected).encode())
        encoded_payload = "" if payload is None else b64(json.dumps(payload).encode())
        signature = sign(self.key, f"{encoded_protected}.{encoded_payload}".encode())
        return json.dumps(
            {"protected": encoded_protected, "payload": encoded_payload, "signature": b64(signature)}
        ).encode()

    def post(self, url: str, payload: Any, what: str, use_jwk: bool = False, accept: str = "") -> Answer:
        """A signed POST (payload None: POST-as-GET). Retries once on badNonce; raises AcmeError on an error."""
        headers = {"Content-Type": "application/jose+json"}
        if accept:
            headers["Accept"] = accept
        for attempt in (1, 2):
            nonce = self._nonce or self._new_nonce()
            self._nonce = None
            answer = self.transport("POST", url, self._signed(url, payload, nonce, use_jwk), headers)
            self._nonce = answer.headers.get("replay-nonce")
            if answer.status < 400:
                return answer
            error = _problem(answer, what)
            if error.kind != "badNonce" or attempt == 2:
                raise error
        raise AssertionError("unreachable")

    # ---- the account

    def register(self, email: str = "") -> str:
        """Create the account, or find the existing one for this key; returns its URL."""
        payload: dict[str, Any] = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        answer = self.post(str(self.directory()["newAccount"]), payload, "Creating the ACME account", use_jwk=True)
        location = answer.headers.get("location")
        if not location:
            raise AcmeError("The ACME authority did not say where the account is.")
        self.account_url = location
        return location

    # ---- a certificate

    def _poll(self, url: str, what: str, done: tuple[str, ...], pending: tuple[str, ...]) -> dict[str, Any]:
        waited = 0.0
        while True:
            data = self.post(url, None, what).json()
            status = data.get("status")
            if status in done:
                return dict(data)
            if status not in pending:
                return dict(data)  # the caller says what went wrong
            if waited >= POLL_LIMIT_SECONDS:
                raise AcmeError(f"{what}: the ACME authority did not finish in {POLL_LIMIT_SECONDS:.0f} seconds.")
            self.sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    def certificate(
        self,
        domain: str,
        key: ec.EllipticCurvePrivateKey,
        publish: Callable[[str, str], None],
        withdraw: Callable[[str], None],
    ) -> str:
        """Order a certificate for `domain` with `key`; returns the chain in PEM. `publish(token, content)` makes
        the gateway serve the key authorization, `withdraw(token)` stops it again (also when this fails)."""
        if not self.account_url:
            raise AcmeError("No ACME account yet.")
        answer = self.post(
            str(self.directory()["newOrder"]),
            {"identifiers": [{"type": "dns", "value": domain}]},
            "Ordering the certificate",
        )
        order, order_url = answer.json(), answer.headers.get("location", "")
        if not order_url:
            raise AcmeError("The ACME authority did not say where the order is.")
        for authorization_url in order.get("authorizations", []):
            self._authorize(str(authorization_url), domain, publish, withdraw)
        finalized = self.post(
            str(order["finalize"]), {"csr": b64(csr(domain, key))}, "Finalizing the certificate"
        ).json()
        if finalized.get("status") != "valid":
            finalized = self._poll(
                order_url, "Waiting for the certificate", ("valid",), ("pending", "ready", "processing")
            )
        if finalized.get("status") != "valid" or not finalized.get("certificate"):
            raise AcmeError(f"The certificate was not issued (order {finalized.get('status')}).")
        chain = self.post(
            str(finalized["certificate"]),
            None,
            "Downloading the certificate",
            accept="application/pem-certificate-chain",
        ).body.decode()
        if "BEGIN CERTIFICATE" not in chain:
            raise AcmeError("The ACME authority sent no certificate.")
        return chain

    def _authorize(
        self, url: str, domain: str, publish: Callable[[str, str], None], withdraw: Callable[[str], None]
    ) -> None:
        authorization = self.post(url, None, "Reading the authorization").json()
        if authorization.get("status") == "valid":
            return  # validated recently: nothing to prove again
        challenge = next((c for c in authorization.get("challenges", []) if c.get("type") == "http-01"), None)
        if challenge is None:
            raise AcmeError("The ACME authority offers no http-01 challenge for this domain.")
        token = str(challenge["token"])
        publish(token, f"{token}.{thumbprint(self.key)}")
        try:
            self.post(str(challenge["url"]), {}, "Starting the domain check")
            result = self._poll(url, "Checking the domain", ("valid",), ("pending", "processing"))
        finally:
            withdraw(token)
        if result.get("status") != "valid":
            problems = [c.get("error", {}) for c in result.get("challenges", []) if c.get("error")]
            detail = str(problems[0].get("detail", "")) if problems else ""
            raise AcmeError(
                f"The ACME authority could not reach {domain} over http: {detail or result.get('status')}"[:500],
                str(problems[0].get("type", "")).rsplit(":", 1)[-1] if problems else "",
            )
