"""Unit tests for memory manager test isolation, singleton lifecycle, and multi-user security."""

import tempfile
import unittest
from pathlib import Path

from core.identity.context import ExecutionContext, ExecutionContextScope
from core.identity.errors import InvalidIdentityError, MissingIdentityError
from core.identity.manager import IdentityManager
from core.memory.database import MemoryDatabase
from core.memory.manager import MemoryManager
from core.tools.memory import (
    _active_memory_manager,
    get_memory_manager,
    recall_memory,
    remember,
    reset_memory_manager,
    set_memory_manager,
)


class TestMemoryIsolationAndFixtures(unittest.TestCase):
    """Verify memory manager dependency injection, singleton isolation, and security boundaries."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        reset_memory_manager()

    def tearDown(self):
        reset_memory_manager()
        self.temp_dir.cleanup()

    # 1. Fresh memory initialization
    def test_fresh_memory_initialization_is_clean_and_isolated(self):
        db_path = self.root / "fresh.db"
        db = MemoryDatabase(db_path)
        self.assertFalse(db.has_registered_users())

        mgr = MemoryManager(db)
        mgr.remember("preferences", "theme", "dark")
        val = mgr.recall("preferences", "theme")
        self.assertEqual(val, "dark")

    # 2. Existing database initialization
    def test_existing_database_with_users_requires_authenticated_identity(self):
        db_path = self.root / "users.db"
        db = MemoryDatabase(db_path)
        im = IdentityManager(database=db, users_root=self.root / "users")
        u1 = im.create_user("alice", password="Password123!", display_name="Alice")
        s1 = im.authenticate("alice", password="Password123!")

        self.assertTrue(db.has_registered_users())

        mgr = MemoryManager(db)
        # Unauthenticated access must fail closed
        with self.assertRaises(MissingIdentityError):
            mgr.recall("general", "note")

        # Authenticated access succeeds
        auth_mgr = MemoryManager(db, identity=s1, identity_manager=im, require_user=True)
        auth_mgr.remember("general", "note", "Alice private note")
        self.assertEqual(auth_mgr.recall("general", "note"), "Alice private note")

    # 3. Unauthenticated access fails closed
    def test_unauthenticated_memory_access_fails_closed(self):
        db_path = self.root / "shared.db"
        db = MemoryDatabase(db_path)
        im = IdentityManager(database=db, users_root=self.root / "users")
        im.create_user("bob", password="Password123!", display_name="Bob")

        mgr = MemoryManager(db)
        with self.assertRaises(MissingIdentityError):
            mgr.remember("general", "secret", "value")

        with self.assertRaises(MissingIdentityError):
            mgr.recall("general", "secret")

    # 4. Cross-user memory isolation
    def test_cross_user_memory_isolation(self):
        db_path = self.root / "isolation.db"
        db = MemoryDatabase(db_path)
        im = IdentityManager(database=db, users_root=self.root / "users")
        im.create_user("alice", password="Password123!", display_name="Alice")
        im.create_user("bob", password="Password123!", display_name="Bob")
        s_alice = im.authenticate("alice", password="Password123!")
        s_bob = im.authenticate("bob", password="Password123!")

        mgr_alice = MemoryManager(db, identity=s_alice, identity_manager=im, require_user=True)
        mgr_bob = MemoryManager(db, identity=s_bob, identity_manager=im, require_user=True)

        mgr_alice.remember("finance", "pin", "1234")
        mgr_bob.remember("finance", "pin", "9876")

        self.assertEqual(mgr_alice.recall("finance", "pin"), "1234")
        self.assertEqual(mgr_bob.recall("finance", "pin"), "9876")

        # Bob cannot see Alice's memories in listing
        alice_mems = mgr_alice.list_memories()
        bob_mems = mgr_bob.list_memories()
        self.assertEqual(len(alice_mems), 1)
        self.assertEqual(alice_mems[0]["value"], "1234")
        self.assertEqual(len(bob_mems), 1)
        self.assertEqual(bob_mems[0]["value"], "9876")

    # 5. Singleton injection and cleanup between tests
    def test_singleton_injection_and_cleanup(self):
        db1 = MemoryDatabase(self.root / "db1.db")
        db2 = MemoryDatabase(self.root / "db2.db")
        mgr1 = MemoryManager(db1)
        mgr2 = MemoryManager(db2)

        set_memory_manager(mgr1)
        remember("cat", "k1", "v1")
        self.assertEqual(recall_memory("cat", "k1"), "v1")
        self.assertEqual(mgr1.recall("cat", "k1"), "v1")
        self.assertIsNone(mgr2.recall("cat", "k1"))

        # Switch injection to db2
        set_memory_manager(mgr2)
        remember("cat", "k2", "v2")
        self.assertEqual(recall_memory("cat", "k2"), "v2")
        self.assertIsNone(mgr1.recall("cat", "k2"))

        # Reset singleton
        reset_memory_manager()
        active = _active_memory_manager()
        self.assertIsNot(active, mgr1)
        self.assertIsNot(active, mgr2)

    # 6. ExecutionContext override takes precedence over singleton
    def test_execution_context_override_takes_precedence_over_singleton(self):
        db_singleton = MemoryDatabase(self.root / "singleton.db")
        db_context = MemoryDatabase(self.root / "context.db")
        im_ctx = IdentityManager(database=db_context, users_root=self.root / "users_ctx")
        u_ctx = im_ctx.create_user("ctx_user", password="Password123!")
        s_ctx = im_ctx.authenticate("ctx_user", password="Password123!")

        mgr_singleton = MemoryManager(db_singleton)
        mgr_context = MemoryManager(db_context, identity=s_ctx, identity_manager=im_ctx, require_user=True)

        set_memory_manager(mgr_singleton)
        remember("test", "key", "singleton_value")

        # Outside context -> hits singleton
        self.assertEqual(recall_memory("test", "key"), "singleton_value")

        # Inside context -> hits context manager
        ctx = ExecutionContext(
            user_id=s_ctx.user_id,
            session_id=s_ctx.session_id,
            workspace_dir=self.root,
            allowed_roots=(self.root,),
            voice_dir=self.root / "voice",
            memory_manager=mgr_context,
            identity=s_ctx,
        )
        with ExecutionContextScope(ctx):
            remember("test", "key", "context_value")
            self.assertEqual(recall_memory("test", "key"), "context_value")

        # Back outside context -> hits singleton again
        self.assertEqual(recall_memory("test", "key"), "singleton_value")

    # 7. Independent database instances do not crosstalk
    def test_independent_database_instances_do_not_crosstalk(self):
        for idx in range(3):
            path = self.root / f"independent_{idx}.db"
            db = MemoryDatabase(path)
            mgr = MemoryManager(db)
            mgr.remember("idx", "val", str(idx))
            self.assertEqual(mgr.recall("idx", "val"), str(idx))

        for idx in range(3):
            path = self.root / f"independent_{idx}.db"
            db = MemoryDatabase(path)
            mgr = MemoryManager(db)
            self.assertEqual(mgr.recall("idx", "val"), str(idx))

    # 8. Repeated test execution is deterministic
    def test_repeated_execution_is_deterministic(self):
        for run in range(5):
            path = self.root / f"repeat_{run}.db"
            db = MemoryDatabase(path)
            mgr = MemoryManager(db)
            for k in range(10):
                mgr.remember("batch", f"k_{k}", f"v_{k}")
            items = mgr.list_memories()
            self.assertEqual(len(items), 10)


if __name__ == "__main__":
    unittest.main()
