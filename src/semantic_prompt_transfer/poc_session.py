from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class PocSession:
    session_id: str
    tenant_id: str
    case_id: str
    label: str
    created_at: float
    expires_at: float
    user_id: str | None = None
    department: str | None = None
    name: str | None = None
    employee_number: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PocSessionGrant:
    token: str
    session: PocSession

    def to_dict(self) -> dict[str, object]:
        value = self.session.to_dict()
        value["access_token"] = self.token
        value["token_type"] = "Bearer"
        return value


class PocSessionManager:
    """Short-lived in-memory access grants for one time-boxed POC runtime."""

    def __init__(
        self,
        access_code: str,
        *,
        tenant_id: str = "poc",
        ttl_seconds: int = 4 * 60 * 60,
        max_sessions: int = 50,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(access_code) < 8:
            raise ValueError("POC access code must contain at least 8 characters")
        if ttl_seconds < 60:
            raise ValueError("POC session TTL must be at least 60 seconds")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self._access_code_digest = self._digest(access_code)
        self.tenant_id = tenant_id
        self.ttl_seconds = int(ttl_seconds)
        self.max_sessions = int(max_sessions)
        self.clock = clock
        self._sessions: dict[str, PocSession] = {}

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _remove_expired(self) -> int:
        now = self.clock()
        expired = [key for key, value in self._sessions.items() if value.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)
        return len(expired)

    def create(self, access_code: str, *, label: str = "POC tester") -> PocSessionGrant:
        self._remove_expired()
        if not hmac.compare_digest(self._digest(access_code), self._access_code_digest):
            raise PermissionError("invalid POC access code")
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError("maximum active POC sessions reached")
        clean_label = " ".join(str(label).split())[:80] or "POC tester"
        token = secrets.token_urlsafe(32)
        token_digest = self._digest(token)
        now = self.clock()
        session_id = secrets.token_hex(12)
        session = PocSession(
            session_id=session_id,
            tenant_id=self.tenant_id,
            case_id=f"poc-{session_id}",
            label=clean_label,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._sessions[token_digest] = session
        return PocSessionGrant(token, session)

    def require(
        self,
        token: str | None,
        *,
        tenant_id: str | None = None,
        case_id: str | None = None,
    ) -> PocSession:
        self._remove_expired()
        if not token:
            raise PermissionError("POC access token is required")
        session = self._sessions.get(self._digest(token))
        if session is None:
            raise PermissionError("invalid or expired POC access token")
        if tenant_id is not None and tenant_id != session.tenant_id:
            raise PermissionError("tenant scope does not match the POC session")
        if case_id is not None and case_id != session.case_id:
            raise PermissionError("case scope does not match the POC session")
        return session

    def revoke(self, token: str) -> PocSession | None:
        return self._sessions.pop(self._digest(token), None)

    def active_count(self) -> int:
        self._remove_expired()
        return len(self._sessions)
