from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .poc_session import PocSession, PocSessionGrant


class PocIdentityService:
    """Ephemeral signup/login registry for a scheduled, non-production POC."""

    EMPLOYEE_NUMBER = re.compile(r"^[0-9A-Za-z_-]{3,32}$")

    def __init__(
        self,
        path: str | Path,
        *,
        tenant_id: str = "poc",
        ttl_seconds: int = 4 * 60 * 60,
        max_users: int = 100,
        password_iterations: int = 120_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds < 60:
            raise ValueError("POC session TTL must be at least 60 seconds")
        if max_users < 1 or password_iterations < 10_000:
            raise ValueError("invalid identity-service limits")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(target), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.RLock()
        self.tenant_id = tenant_id
        self.ttl_seconds = int(ttl_seconds)
        self.max_users = int(max_users)
        self.password_iterations = int(password_iterations)
        self.clock = clock
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS poc_users (
                    user_id TEXT PRIMARY KEY,
                    employee_number TEXT NOT NULL UNIQUE,
                    department TEXT NOT NULL,
                    name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poc_sessions (
                    token_hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL REFERENCES poc_users(user_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            self.connection.commit()

    @staticmethod
    def _clean(value: str, field: str, max_length: int) -> str:
        cleaned = " ".join(str(value or "").split())
        if not cleaned:
            raise ValueError(f"{field} is required")
        if len(cleaned) > max_length:
            raise ValueError(f"{field} is too long")
        return cleaned

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _password_hash(self, password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, self.password_iterations
        )

    @staticmethod
    def _case_id(employee_number: str) -> str:
        digest = hashlib.sha256(employee_number.encode("utf-8")).hexdigest()[:24]
        return f"poc-{digest}"

    def register(self, *, department: str, name: str, employee_number: str) -> dict[str, Any]:
        department = self._clean(department, "department", 80)
        name = self._clean(name, "name", 80)
        employee_number = self._clean(employee_number, "employee_number", 32)
        if not self.EMPLOYEE_NUMBER.fullmatch(employee_number):
            raise ValueError("employee_number must use 3-32 letters, digits, '_' or '-'")
        with self._lock:
            count = int(self.connection.execute("SELECT COUNT(*) FROM poc_users").fetchone()[0])
            if count >= self.max_users:
                raise RuntimeError("maximum POC user count reached")
            salt = secrets.token_bytes(16)
            password_hash = self._password_hash(employee_number, salt)
            try:
                self.connection.execute(
                    "INSERT INTO poc_users VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        employee_number,
                        employee_number,
                        department,
                        name,
                        salt,
                        password_hash,
                        self.clock(),
                    ),
                )
                self.connection.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("employee number is already registered") from exc
        return {
            "user_id": employee_number,
            "department": department,
            "name": name,
            "employee_number": employee_number,
            "initial_password_rule": "EMPLOYEE_NUMBER",
        }

    def _remove_expired(self) -> int:
        cursor = self.connection.execute(
            "DELETE FROM poc_sessions WHERE expires_at<=?", (self.clock(),)
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def login(self, user_id: str, password: str) -> PocSessionGrant:
        with self._lock:
            self._remove_expired()
            row = self.connection.execute(
                "SELECT * FROM poc_users WHERE user_id=?", (str(user_id),)
            ).fetchone()
            supplied = self._password_hash(str(password), row["password_salt"] if row else b"0" * 16)
            if row is None or not hmac.compare_digest(supplied, row["password_hash"]):
                raise PermissionError("invalid user ID or password")
            token = secrets.token_urlsafe(32)
            now = self.clock()
            session_id = secrets.token_hex(12)
            case_id = self._case_id(row["employee_number"])
            self.connection.execute(
                "INSERT INTO poc_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self._token_hash(token),
                    session_id,
                    row["user_id"],
                    self.tenant_id,
                    case_id,
                    now,
                    now + self.ttl_seconds,
                ),
            )
            self.connection.commit()
            session = PocSession(
                session_id=session_id,
                tenant_id=self.tenant_id,
                case_id=case_id,
                label=f"{row['department']} / {row['name']}",
                created_at=now,
                expires_at=now + self.ttl_seconds,
                user_id=row["user_id"],
                department=row["department"],
                name=row["name"],
                employee_number=row["employee_number"],
            )
            return PocSessionGrant(token, session)

    def require(
        self,
        token: str | None,
        *,
        tenant_id: str | None = None,
        case_id: str | None = None,
    ) -> PocSession:
        with self._lock:
            self._remove_expired()
            if not token:
                raise PermissionError("POC access token is required")
            row = self.connection.execute(
                """SELECT s.*, u.department, u.name, u.employee_number
                   FROM poc_sessions s JOIN poc_users u ON u.user_id=s.user_id
                   WHERE s.token_hash=?""",
                (self._token_hash(token),),
            ).fetchone()
            if row is None:
                raise PermissionError("invalid or expired POC access token")
            if tenant_id is not None and tenant_id != row["tenant_id"]:
                raise PermissionError("tenant scope does not match the POC session")
            if case_id is not None and case_id != row["case_id"]:
                raise PermissionError("case scope does not match the POC session")
            return PocSession(
                session_id=row["session_id"],
                tenant_id=row["tenant_id"],
                case_id=row["case_id"],
                label=f"{row['department']} / {row['name']}",
                created_at=float(row["created_at"]),
                expires_at=float(row["expires_at"]),
                user_id=row["user_id"],
                department=row["department"],
                name=row["name"],
                employee_number=row["employee_number"],
            )

    def revoke(self, token: str) -> PocSession | None:
        with self._lock:
            try:
                session = self.require(token)
            except PermissionError:
                return None
            self.connection.execute(
                "DELETE FROM poc_sessions WHERE token_hash=?", (self._token_hash(token),)
            )
            self.connection.commit()
            return session

    def active_count(self) -> int:
        with self._lock:
            self._remove_expired()
            return int(self.connection.execute("SELECT COUNT(*) FROM poc_sessions").fetchone()[0])

    def user_count(self) -> int:
        with self._lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM poc_users").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self.connection.close()

