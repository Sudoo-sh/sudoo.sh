"""A stand-in ACME authority (RFC 8555) for the tests: never Let's Encrypt.

It checks every request the way an authority does (a JWS signed with the account key, ES256, a fresh nonce, the
right URL), "validates" an http-01 challenge by reading the answer the panel published for the gateway, and signs
the certificate request with a test CA.
"""

from __future__ import annotations

import base64
import datetime
import json
import secrets
from collections.abc import Callable
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID

from sudoo.instances import acme

BASE = "https://acme.test-authority.invalid"


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_key(jwk: dict[str, str]) -> ec.EllipticCurvePublicKey:
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_unb64(jwk["x"]), "big"), int.from_bytes(_unb64(jwk["y"]), "big"), ec.SECP256R1()
    )
    return numbers.public_key()


class FakeAcme:
    def __init__(self, answer_for: Callable[[str], bytes | None], lifetime_days: int = 90) -> None:
        self.answer_for = answer_for  # token -> what the "gateway" serves for it
        self.lifetime_days = lifetime_days
        self.nonces: set[str] = set()
        self.accounts: dict[str, ec.EllipticCurvePublicKey] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.authorizations: dict[str, dict[str, Any]] = {}
        self.certificates: dict[str, str] = {}
        self.requests: list[str] = []
        self.refuse_challenge = ""  # an error detail: the next validation fails with it
        self.bad_nonce_once = False
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test Authority"),
                          x509.NameAttribute(NameOID.COMMON_NAME, "Fake R1")])  # fmt: skip
        now = datetime.datetime.now(datetime.UTC)
        self.ca = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.ca_key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )  # fmt: skip

    # ---- plumbing

    def _nonce(self) -> str:
        nonce = secrets.token_urlsafe(16)
        self.nonces.add(nonce)
        return nonce

    def _answer(self, status: int, body: Any = None, location: str = "", raw: bytes | None = None) -> acme.Answer:
        headers = {"replay-nonce": self._nonce()}
        if location:
            headers["location"] = location
        return acme.Answer(status, headers, raw if raw is not None else json.dumps(body or {}).encode())

    def _problem(self, status: int, kind: str, detail: str) -> acme.Answer:
        return self._answer(status, {"type": f"urn:ietf:params:acme:error:{kind}", "detail": detail})

    def _verify(self, url: str, body: bytes) -> tuple[Any, str | None, acme.Answer | None]:
        """(payload, account URL, problem): checks signature, nonce and URL like an authority."""
        jws = json.loads(body)
        protected = json.loads(_unb64(jws["protected"]))
        if protected.get("url") != url or protected.get("alg") != "ES256":
            return None, None, self._problem(400, "malformed", "wrong url or alg")
        if self.bad_nonce_once:
            self.bad_nonce_once = False
            return None, None, self._problem(400, "badNonce", "try again")
        if protected.get("nonce") not in self.nonces:
            return None, None, self._problem(400, "badNonce", "unknown nonce")
        self.nonces.discard(protected["nonce"])
        account = protected.get("kid")
        key = self.accounts.get(account) if account else _public_key(protected["jwk"])
        if key is None:
            return None, None, self._problem(400, "accountDoesNotExist", "no such account")
        signature = _unb64(jws["signature"])
        der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        key.verify(der, f"{jws['protected']}.{jws['payload']}".encode(), ec.ECDSA(hashes.SHA256()))
        payload = json.loads(_unb64(jws["payload"])) if jws["payload"] else None
        if account is None:
            account = f"{BASE}/acct/{len(self.accounts) + 1}"
            self.accounts.setdefault(account, key)
        return payload, account, None

    # ---- the transport

    def __call__(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> acme.Answer:
        self.requests.append(f"{method} {url.removeprefix(BASE)}")
        if method == "GET" and url == f"{BASE}/directory":
            return acme.Answer(200, {}, json.dumps({
                "newNonce": f"{BASE}/new-nonce", "newAccount": f"{BASE}/new-account", "newOrder": f"{BASE}/new-order",
            }).encode())  # fmt: skip
        if method == "HEAD" and url == f"{BASE}/new-nonce":
            return self._answer(200)
        assert method == "POST" and body is not None, (method, url)
        payload, account, problem = self._verify(url, body)
        if problem:
            return problem
        path = url.removeprefix(BASE)
        if path == "/new-account":
            assert payload["termsOfServiceAgreed"] is True
            return self._answer(201, {"status": "valid"}, location=str(account))
        if path == "/new-order":
            domain = payload["identifiers"][0]["value"]
            number = str(len(self.orders) + 1)
            token = secrets.token_urlsafe(32)
            self.authorizations[number] = {"status": "pending", "domain": domain, "token": token, "account": account,
                                           "error": None}  # fmt: skip
            self.orders[number] = {"status": "pending", "domain": domain, "certificate": None}
            return self._answer(201, self._order(number), location=f"{BASE}/order/{number}")
        kind, number = path.strip("/").split("/")
        if kind == "authz":
            return self._answer(200, self._authorization(number))
        if kind == "chall":
            authorization = self.authorizations[number]
            expected = f"{authorization['token']}.{acme.thumbprint_of(self.accounts[str(account)])}".encode()
            served = self.answer_for(authorization["token"])
            if self.refuse_challenge or served != expected:
                authorization["status"] = "invalid"
                authorization["error"] = {"type": "urn:ietf:params:acme:error:connection",
                                          "detail": self.refuse_challenge or "the answer did not match"}  # fmt: skip
                self.refuse_challenge = ""
            else:
                authorization["status"] = "valid"
                self.orders[number]["status"] = "ready"
            return self._answer(200, {"status": "processing"})
        if kind == "finalize":
            order = self.orders[number]
            if order["status"] != "ready":
                return self._problem(403, "orderNotReady", "not ready")
            request = x509.load_der_x509_csr(_unb64(payload["csr"]))
            names = request.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            assert names.get_values_for_type(x509.DNSName) == [order["domain"]]
            now = datetime.datetime.now(datetime.UTC)
            certificate = (
                x509.CertificateBuilder().subject_name(request.subject).issuer_name(self.ca.subject)
                .public_key(request.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=self.lifetime_days))
                .add_extension(names, critical=False).sign(self.ca_key, hashes.SHA256())
            )  # fmt: skip
            pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in (certificate, self.ca)).decode()
            self.certificates[number] = pem
            order.update(status="valid", certificate=f"{BASE}/cert/{number}")
            return self._answer(200, self._order(number))
        if kind == "order":
            return self._answer(200, self._order(number))
        if kind == "cert":
            return self._answer(200, raw=self.certificates[number].encode())
        raise AssertionError(path)

    def _order(self, number: str) -> dict[str, Any]:
        order = self.orders[number]
        shown = {"status": order["status"], "authorizations": [f"{BASE}/authz/{number}"],
                 "finalize": f"{BASE}/finalize/{number}"}  # fmt: skip
        if order["certificate"]:
            shown["certificate"] = order["certificate"]
        return shown

    def _authorization(self, number: str) -> dict[str, Any]:
        authorization = self.authorizations[number]
        challenge: dict[str, Any] = {"type": "http-01", "url": f"{BASE}/chall/{number}",
                                     "token": authorization["token"]}  # fmt: skip
        if authorization["error"]:
            challenge["error"] = authorization["error"]
        return {"status": authorization["status"], "identifier": {"type": "dns", "value": authorization["domain"]},
                "challenges": [challenge, {"type": "dns-01", "url": f"{BASE}/x", "token": "unused"}]}  # fmt: skip
