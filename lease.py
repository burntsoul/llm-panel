# lease.py
"""
LeaseManager: persistent lease management for LLM VM access.

A lease represents a client's right to use the LLM server. Leases:
- Are created when a client needs access
- Have a TTL (time-to-live) in seconds
- Can be refreshed to extend expiry
- Are automatically cleaned up when expired
- Are persisted to disk so restarts don't instantly forget activity
"""

from __future__ import annotations

import json
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import threading

from config import settings


logger = logging.getLogger(__name__)


class Lease:
    """Represents a single lease."""

    def __init__(
        self,
        lease_id: str,
        client_id: str,
        purpose: str,
        ttl_seconds: int,
        created_at: datetime | None = None,
        last_seen: datetime | None = None,
        expires_at: datetime | None = None,
    ):
        self.lease_id = lease_id
        self.client_id = client_id
        self.purpose = purpose
        self.ttl_seconds = ttl_seconds
        self.created_at = created_at or datetime.utcnow()
        self.last_seen = last_seen or self.created_at
        self.expires_at = expires_at or self.created_at + timedelta(seconds=ttl_seconds)

    def is_expired(self) -> bool:
        """Check if lease has expired."""
        return datetime.utcnow() >= self.expires_at

    def refresh(self, ttl_seconds: Optional[int] = None) -> None:
        """Extend the lease expiry and update last_seen."""
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        self.ttl_seconds = ttl
        self.last_seen = datetime.utcnow()
        self.expires_at = self.last_seen + timedelta(seconds=ttl)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON."""
        return {
            "lease_id": self.lease_id,
            "client_id": self.client_id,
            "purpose": self.purpose,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Lease:
        """Deserialize from dict."""
        return Lease(
            lease_id=data["lease_id"],
            client_id=data["client_id"],
            purpose=data["purpose"],
            ttl_seconds=data["ttl_seconds"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class LeaseManager:
    """
    Manages leases with in-memory storage and periodic disk persistence.

    Thread-safe using a lock. Periodically saves state to disk so
    that VM restarts don't instantly forget activity.
    """

    def __init__(self, persist_path: Optional[str] = None):
        """Initialize the lease manager.

        Args:
            persist_path: Path to persist leases to (default: state directory)
        """
        self.persist_path = Path(
            persist_path or settings.STATE_PATH
        ).parent / "leases.json"
        self._leases: Dict[str, Lease] = {}
        self._lock = threading.RLock()
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load persisted leases from disk."""
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            leases = data.get("leases", {})
            for lease_id, lease_data in leases.items():
                lease = Lease.from_dict(lease_data)
                # Only restore non-expired leases
                if not lease.is_expired():
                    self._leases[lease_id] = lease
            logger.info(f"Loaded {len(self._leases)} leases from disk")
        except Exception as e:
            logger.warning(f"Failed to load leases from disk: {e}")

    def _save_to_disk(self) -> None:
        """Save all leases to disk."""
        try:
            data = {
                "leases": {
                    lease_id: lease.to_dict()
                    for lease_id, lease in self._leases.items()
                }
            }
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.persist_path)
        except Exception as e:
            logger.error(f"Failed to save leases to disk: {e}")

    def create_lease(
        self, client_id: str, purpose: str, ttl_seconds: int
    ) -> Lease:
        """Create a new lease.

        Args:
            client_id: Identifier for the client
            purpose: Description of what the lease is for
            ttl_seconds: Time-to-live in seconds

        Returns:
            The created Lease object
        """
        with self._lock:
            lease_id = str(uuid.uuid4())
            lease = Lease(
                lease_id=lease_id,
                client_id=client_id,
                purpose=purpose,
                ttl_seconds=ttl_seconds,
            )
            self._leases[lease_id] = lease
            self._save_to_disk()
            logger.info(
                f"Created lease {lease_id} for client {client_id} "
                f"(purpose: {purpose}, ttl: {ttl_seconds}s)"
            )
            return lease

    def get_lease(self, lease_id: str) -> Optional[Lease]:
        """Get a lease by ID.

        Args:
            lease_id: The lease ID

        Returns:
            The Lease object, or None if not found or expired
        """
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is not None and lease.is_expired():
                del self._leases[lease_id]
                self._save_to_disk()
                logger.info(f"Lease {lease_id} expired and removed")
                return None
            return lease

    def refresh_lease(self, lease_id: str, ttl_seconds: Optional[int] = None) -> bool:
        """Refresh a lease (extend expiry and update last_seen).

        Args:
            lease_id: The lease ID
            ttl_seconds: New TTL (or keep existing if None)

        Returns:
            True if successful, False if lease not found or expired
        """
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.is_expired():
                if lease is not None:
                    del self._leases[lease_id]
                    self._save_to_disk()
                return False
            lease.refresh(ttl_seconds)
            self._save_to_disk()
            logger.info(f"Refreshed lease {lease_id} (expires at {lease.expires_at})")
            return True

    def release_lease(self, lease_id: str) -> bool:
        """Remove a lease immediately.

        Args:
            lease_id: The lease ID

        Returns:
            True if lease was removed, False if not found
        """
        with self._lock:
            if lease_id in self._leases:
                del self._leases[lease_id]
                self._save_to_disk()
                logger.info(f"Released lease {lease_id}")
                return True
            return False

    def get_active_leases(self) -> List[Lease]:
        """Get all non-expired leases.

        Returns:
            List of active Lease objects
        """
        with self._lock:
            # Remove expired leases
            expired = [
                lease_id
                for lease_id, lease in self._leases.items()
                if lease.is_expired()
            ]
            for lease_id in expired:
                del self._leases[lease_id]
            if expired:
                self._save_to_disk()

            return list(self._leases.values())

    def cleanup_expired(self) -> int:
        """Remove all expired leases.

        Returns:
            Number of leases removed
        """
        with self._lock:
            before = len(self._leases)
            self._leases = {
                lease_id: lease
                for lease_id, lease in self._leases.items()
                if not lease.is_expired()
            }
            after = len(self._leases)
            removed = before - after
            if removed > 0:
                self._save_to_disk()
                logger.info(f"Cleaned up {removed} expired leases")
            return removed

    def has_active_leases(self) -> bool:
        """Check if there are any active leases."""
        with self._lock:
            self._leases = {
                lease_id: lease
                for lease_id, lease in self._leases.items()
                if not lease.is_expired()
            }
            return len(self._leases) > 0

    def force_save(self) -> None:
        """Force immediate save to disk (useful for testing)."""
        with self._lock:
            self._save_to_disk()


# Global instance
_lease_manager: Optional[LeaseManager] = None


def get_lease_manager() -> LeaseManager:
    """Get or create the global LeaseManager instance."""
    global _lease_manager
    if _lease_manager is None:
        _lease_manager = LeaseManager()
    return _lease_manager
