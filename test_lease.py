# test_lease.py
"""
Unit tests for LeaseManager.

Run with: python -m pytest test_lease.py -v
"""

import unittest
import tempfile
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

from lease import Lease, LeaseManager


class TestLease(unittest.TestCase):
    """Test the Lease class."""

    def test_lease_creation(self):
        """Test basic lease creation."""
        lease = Lease(
            lease_id="test-123",
            client_id="client-1",
            purpose="testing",
            ttl_seconds=3600,
        )
        self.assertEqual(lease.lease_id, "test-123")
        self.assertEqual(lease.client_id, "client-1")
        self.assertEqual(lease.purpose, "testing")
        self.assertFalse(lease.is_expired())

    def test_lease_expiry(self):
        """Test lease expiry detection."""
        lease = Lease(
            lease_id="test-123",
            client_id="client-1",
            purpose="testing",
            ttl_seconds=1,
        )
        self.assertFalse(lease.is_expired())

        # Set expiry to the past
        lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.assertTrue(lease.is_expired())

    def test_lease_refresh(self):
        """Test lease refresh extends expiry."""
        lease = Lease(
            lease_id="test-123",
            client_id="client-1",
            purpose="testing",
            ttl_seconds=10,
        )
        original_expires_at = lease.expires_at
        original_last_seen = lease.last_seen

        time.sleep(0.1)
        lease.refresh(ttl_seconds=20)

        self.assertGreater(lease.expires_at, original_expires_at)
        self.assertGreater(lease.last_seen, original_last_seen)
        self.assertEqual(lease.ttl_seconds, 20)

    def test_lease_serialization(self):
        """Test lease to/from dict serialization."""
        lease = Lease(
            lease_id="test-123",
            client_id="client-1",
            purpose="testing",
            ttl_seconds=3600,
        )
        data = lease.to_dict()

        # Verify structure
        self.assertIn("lease_id", data)
        self.assertIn("client_id", data)
        self.assertIn("created_at", data)

        # Deserialize
        lease2 = Lease.from_dict(data)
        self.assertEqual(lease2.lease_id, lease.lease_id)
        self.assertEqual(lease2.client_id, lease.client_id)
        self.assertEqual(lease2.expires_at, lease.expires_at)


class TestLeaseManager(unittest.TestCase):
    """Test the LeaseManager class."""

    def setUp(self):
        """Create a temporary file for testing."""
        self.temp_dir = tempfile.mkdtemp()
        # Note: persist_path should be a directory or config path
        # The LeaseManager will create leases.json in the parent directory
        self.state_path = Path(self.temp_dir) / "state.json"
        self.persist_path = self.state_path.parent / "leases.json"

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_lease_manager_creation(self):
        """Test LeaseManager initialization."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        self.assertIsNotNone(mgr)
        self.assertEqual(len(mgr.get_active_leases()), 0)

    def test_create_lease(self):
        """Test creating a lease."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease = mgr.create_lease(
            client_id="client-1",
            purpose="testing",
            ttl_seconds=3600,
        )

        self.assertIsNotNone(lease.lease_id)
        self.assertEqual(lease.client_id, "client-1")
        self.assertFalse(lease.is_expired())

    def test_get_lease(self):
        """Test retrieving a lease."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease1 = mgr.create_lease(
            client_id="client-1",
            purpose="testing",
            ttl_seconds=3600,
        )

        lease2 = mgr.get_lease(lease1.lease_id)
        self.assertIsNotNone(lease2)
        self.assertEqual(lease2.lease_id, lease1.lease_id)

    def test_get_nonexistent_lease(self):
        """Test retrieving a nonexistent lease."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease = mgr.get_lease("nonexistent")
        self.assertIsNone(lease)

    def test_refresh_lease(self):
        """Test refreshing a lease."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease1 = mgr.create_lease(
            client_id="client-1",
            purpose="testing",
            ttl_seconds=10,
        )

        original_expires_at = lease1.expires_at
        time.sleep(0.1)

        ok = mgr.refresh_lease(lease1.lease_id, ttl_seconds=20)
        self.assertTrue(ok)

        lease2 = mgr.get_lease(lease1.lease_id)
        self.assertGreater(lease2.expires_at, original_expires_at)

    def test_refresh_expired_lease(self):
        """Test refreshing an expired lease fails."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease = mgr.create_lease(
            client_id="client-1",
            purpose="testing",
            ttl_seconds=1,
        )

        # Expire the lease manually
        lease.expires_at = datetime.utcnow() - timedelta(seconds=1)

        ok = mgr.refresh_lease(lease.lease_id)
        self.assertFalse(ok)

    def test_release_lease(self):
        """Test releasing a lease."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        lease = mgr.create_lease(
            client_id="client-1",
            purpose="testing",
            ttl_seconds=3600,
        )

        ok = mgr.release_lease(lease.lease_id)
        self.assertTrue(ok)

        lease2 = mgr.get_lease(lease.lease_id)
        self.assertIsNone(lease2)

    def test_get_active_leases(self):
        """Test getting active leases."""
        mgr = LeaseManager(persist_path=str(self.state_path))

        lease1 = mgr.create_lease("client-1", "test", 3600)
        lease2 = mgr.create_lease("client-2", "test", 3600)

        active = mgr.get_active_leases()
        self.assertEqual(len(active), 2)

    def test_get_active_leases_filters_expired(self):
        """Test that get_active_leases filters out expired leases."""
        mgr = LeaseManager(persist_path=str(self.state_path))

        lease1 = mgr.create_lease("client-1", "test", 3600)
        lease2 = mgr.create_lease("client-2", "test", 1)

        # Expire lease2
        lease2.expires_at = datetime.utcnow() - timedelta(seconds=1)

        active = mgr.get_active_leases()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].lease_id, lease1.lease_id)

    def test_has_active_leases(self):
        """Test checking for active leases."""
        mgr = LeaseManager(persist_path=str(self.state_path))
        self.assertFalse(mgr.has_active_leases())

        mgr.create_lease("client-1", "test", 3600)
        self.assertTrue(mgr.has_active_leases())

    def test_cleanup_expired(self):
        """Test cleanup of expired leases."""
        mgr = LeaseManager(persist_path=str(self.state_path))

        lease1 = mgr.create_lease("client-1", "test", 3600)
        lease2 = mgr.create_lease("client-2", "test", 1)

        # Expire lease2
        lease2.expires_at = datetime.utcnow() - timedelta(seconds=1)

        removed = mgr.cleanup_expired()
        self.assertEqual(removed, 1)

        active = mgr.get_active_leases()
        self.assertEqual(len(active), 1)

    def test_persistence(self):
        """Test that leases persist to disk."""
        # Create manager and add leases
        mgr1 = LeaseManager(persist_path=str(self.state_path))
        lease1 = mgr1.create_lease("client-1", "test", 3600)
        lease_id_1 = lease1.lease_id
        mgr1.force_save()

        # Verify file was created (or will be on next save)
        # The file may exist or be created
        import time
        time.sleep(0.1)  # Small delay for file operations
        if not self.persist_path.exists():
            # If not, trigger a save
            mgr1.force_save()
        
        self.assertTrue(self.persist_path.exists(), 
                       f"Persist file not found at {self.persist_path}")

        # Create a new manager instance and verify it loads the lease
        mgr2 = LeaseManager(persist_path=str(self.state_path))
        lease2 = mgr2.get_lease(lease_id_1)

        self.assertIsNotNone(lease2)
        self.assertEqual(lease2.lease_id, lease_id_1)
        self.assertEqual(lease2.client_id, "client-1")

    def test_persistence_ignores_expired(self):
        """Test that expired leases are not restored from disk."""
        # Create manager and add leases
        mgr1 = LeaseManager(persist_path=str(self.state_path))
        lease1 = mgr1.create_lease("client-1", "test", 1)
        lease_id_1 = lease1.lease_id

        # Expire it
        lease1.expires_at = datetime.utcnow() - timedelta(seconds=1)
        mgr1.force_save()

        # Create a new manager instance and verify expired lease is not loaded
        mgr2 = LeaseManager(persist_path=str(self.state_path))
        lease2 = mgr2.get_lease(lease_id_1)

        self.assertIsNone(lease2)


if __name__ == "__main__":
    unittest.main()
