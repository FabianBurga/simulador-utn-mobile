from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import hmac

ADMIN_VERSION = "P2-M5B.3"


def verify_admin_password(password: str, encoded_hash: str) -> bool:
    try:
        algo, raw_iterations, salt_hex, expected_hex = str(encoded_hash).split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


@dataclass
class AdminDataStore:
    client: Any

    @classmethod
    def from_supabase(cls, url: str, secret_key: str) -> "AdminDataStore":
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("Falta instalar el paquete 'supabase'.") from exc
        return cls(create_client(url, secret_key))

    def _select(
        self,
        table: str,
        *,
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        desc: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        q = self.client.table(table).select("*")
        for key, value in (filters or {}).items():
            if value is not None:
                q = q.eq(key, value)
        if order:
            q = q.order(order, desc=desc)
        if limit:
            q = q.limit(int(limit))
        result = q.execute()
        return list(result.data or [])

    def students(self):
        return self._select(
            "p2_admin_student_summary",
            order="last_seen_at",
            desc=True,
            limit=5000,
        )

    def sessions(self, user_id: str | None = None, limit: int = 5000):
        return self._select(
            "p2_admin_sessions",
            filters={"user_id": user_id} if user_id else None,
            order="started_at",
            desc=True,
            limit=limit,
        )

    def live_sessions(self):
        rows = self._select(
            "p2_admin_sessions",
            order="last_seen_at",
            desc=True,
            limit=500,
        )
        return [
            row for row in rows
            if row.get("status") in {"active", "idle"}
            and row.get("observed_state") in {"active", "idle"}
        ]

    def daily_metrics(self, days: int = 30):
        rows = self._select(
            "p2_admin_daily_metrics",
            order="day",
            desc=False,
            limit=3660,
        )
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(1, int(days)) - 1)
        output = []
        for row in rows:
            day = row.get("day")
            if not day:
                continue
            try:
                parsed = datetime.fromisoformat(str(day)).date()
            except Exception:
                continue
            if parsed >= cutoff:
                output.append(row)
        return output

    def attempts(self, user_id: str | None = None, limit: int = 5000):
        return self._select(
            "p2_admin_attempts",
            filters={"user_id": user_id} if user_id else None,
            order="started_at",
            desc=True,
            limit=limit,
        )

    def questions(self):
        return self._select(
            "p2_admin_question_metrics",
            order="attempts_seen",
            desc=True,
            limit=10000,
        )

    def areas(self):
        return self._select(
            "p2_admin_area_metrics",
            order="responses",
            desc=True,
            limit=1000,
        )

    def retention(self):
        return self._select(
            "p2_admin_retention",
            order="first_activity_day",
            desc=True,
            limit=5000,
        )

    def funnel(self):
        rows = self._select("p2_admin_funnel", limit=1)
        return rows[0] if rows else {}

    def health(self):
        rows = self._select("p2_admin_system_health", limit=1)
        return rows[0] if rows else {}

    def data_quality(self):
        rows = self._select("p2_admin_data_quality", limit=1)
        return rows[0] if rows else {}

    def user_timeline(self, user_id: str, limit: int = 500):
        return self._select(
            "p2_analytics_events",
            filters={"user_id": user_id},
            order="occurred_at",
            desc=True,
            limit=limit,
        )

    def attempt_items(self, attempt_id: str):
        return self._select(
            "p2_analytics_attempt_items",
            filters={"attempt_id": attempt_id},
            order="question_order",
            desc=False,
            limit=1000,
        )


def seconds_human(value: Any) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except Exception:
        seconds = 0
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return "—"


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    try:
        n = float(numerator or 0)
        d = float(denominator or 0)
    except Exception:
        return None
    if d <= 0:
        return None
    return 100.0 * n / d
