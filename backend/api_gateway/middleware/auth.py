"""Authentification gateway — JWT HS256 + clés API pour agents externes.

- JWT : python-jose SI disponible, sinon implémentation HMAC maison (même
  format base64url(header).payload.signature, algorithme HS256) ;
- clés API : préfixe « pcbak_ », stockées en mémoire UNIQUEMENT sous forme de
  hash sha256 — pratique pour Claude/Cursor/Devin et les plugins EDA.
`authenticate(headers)` est le point d'entrée unique des middlewares.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import GatewaySettings

try:  # python-jose optionnel (requirements.txt)
    from jose import jwt as _jose_jwt   # type: ignore
except ImportError:
    _jose_jwt = None

API_KEY_PREFIX = "pcbak_"
_ALGORITHM = "HS256"


class AuthError(Exception):
    """Erreur d'authentification — statut HTTP transportable."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Claims:
    """Payload décodé d'un JWT — sous/scopes/tier/exp."""

    sub: str
    scopes: list[str] = field(default_factory=list)
    tier: str = "free"
    exp: int = 0
    iat: int = 0
    iss: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Claims":
        return cls(sub=str(payload.get("sub", "")),
                   scopes=list(payload.get("scopes", []) or []),
                   tier=str(payload.get("tier", "free")),
                   exp=int(payload.get("exp", 0)), iat=int(payload.get("iat", 0)),
                   iss=str(payload.get("iss", "")))


@dataclass
class Identity:
    """Identité authentifiée — consommée par les routes et le rate limiter."""

    sub: str
    scopes: list[str]
    tier: str
    via: str                # "jwt" | "api_key"


# ---- JWT HS256 (fallback maison) ------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _jwt_encode_homegrown(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": _ALGORITHM, "typ": "JWT"}
    segments = [_b64url(json.dumps(header, separators=(",", ":")).encode()),
                _b64url(json.dumps(payload, separators=(",", ":")).encode())]
    signing_input = ".".join(segments).encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)


def _jwt_decode_homegrown(token: str, secret: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise AuthError(401, "jeton malformé") from exc
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), signature_b64):
        raise AuthError(401, "signature JWT invalide")
    try:
        return json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError(401, "payload JWT illisible") from exc


# ---- Service d'authentification ---------------------------------------------------

class AuthService:
    """Émission/vérification des JWT et gestion des clés API (hash sha256)."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        # clé = sha256(api_key) → fiche {name, scopes, tier} — JAMAIS la clé en clair
        self._api_keys: dict[str, dict[str, Any]] = {}

    # ---- JWT ---------------------------------------------------------------
    def issue_token(self, user: str, scopes: list[str] | None = None,
                    tier: str = "free", expires_min: int | None = None) -> str:
        """Émet un JWT HS256 (python-jose si dispo sinon HMAC maison)."""
        now = int(time.time())
        payload = {"sub": user, "scopes": scopes or ["read", "write"], "tier": tier,
                   "iat": now, "exp": now + (expires_min or self.settings.jwt_expires_min) * 60,
                   "iss": self.settings.jwt_issuer}
        if _jose_jwt is not None:
            return str(_jose_jwt.encode(payload, self.settings.jwt_secret, algorithm=_ALGORITHM))
        return _jwt_encode_homegrown(payload, self.settings.jwt_secret)

    def verify(self, token: str) -> Claims:
        """Vérifie signature + expiration → Claims (AuthError 401 sinon)."""
        try:
            if _jose_jwt is not None:
                payload = dict(_jose_jwt.decode(token, self.settings.jwt_secret,
                                                algorithms=[_ALGORITHM]))
            else:
                payload = _jwt_decode_homegrown(token, self.settings.jwt_secret)
        except AuthError:
            raise
        except Exception as exc:  # jose lève JWTError (importé conditionnellement)
            raise AuthError(401, f"jeton rejeté : {exc}") from exc
        claims = Claims.from_payload(payload)
        if claims.exp and claims.exp < time.time():
            raise AuthError(401, "jeton expiré")
        return claims

    # ---- clés API --------------------------------------------------------------
    def register_api_key(self, name: str, scopes: list[str] | None = None,
                         tier: str = "free") -> str:
        """Crée une clé « pcbak_… » — la clé en clair n'est visible qu'ICI."""
        api_key = API_KEY_PREFIX + secrets.token_hex(16)
        digest = self._hash_key(api_key)
        self._api_keys[digest] = {"name": name, "scopes": scopes or ["read", "write"],
                                  "tier": tier}
        return api_key

    def _hash_key(self, api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()

    # ---- point d'entrée middleware ------------------------------------------------
    def authenticate(self, headers: dict[str, str]) -> Identity:
        """Authentifie via `Authorization: Bearer <jwt>` ou `X-API-Key: pcbak_…`."""
        authorization = _header(headers, "authorization")
        if authorization.lower().startswith("bearer "):
            claims = self.verify(authorization[7:].strip())
            return Identity(sub=claims.sub, scopes=claims.scopes, tier=claims.tier, via="jwt")
        api_key = _header(headers, "x-api-key")
        if api_key.startswith(API_KEY_PREFIX):
            record = self._api_keys.get(self._hash_key(api_key))
            if record is None:
                raise AuthError(401, "clé API inconnue")
            return Identity(sub=record["name"], scopes=record["scopes"],
                            tier=record["tier"], via="api_key")
        raise AuthError(401, "authentification requise (JWT ou X-API-Key)")


def _header(headers: dict[str, str], name: str) -> str:
    """Lecture d'en-tête insensible à la casse."""
    for key, value in headers.items():
        if key.lower() == name:
            return str(value)
    return ""
