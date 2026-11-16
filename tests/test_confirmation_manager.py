
import threading
import unittest

from core.security.confirmation import ConfirmationManager


class TestConfirmationManager(unittest.TestCase):

    def setUp(self):
        self.current_time = 1000.0
        self.manager = ConfirmationManager(
            ttl_seconds=120,
            clock=lambda: self.current_time,
        )
        self.parameters = {
            "category": "personal",
            "key": "my_name",
            "value": "Tukaram",
        }

    def test_valid_token_is_accepted_once(self):
        token = self.manager.issue("remember", self.parameters)

        accepted, _ = self.manager.consume(
            token, "remember", self.parameters
        )

        self.assertTrue(accepted)

        accepted_again, _ = self.manager.consume(
            token, "remember", self.parameters
        )

        self.assertFalse(accepted_again)

    def test_wrong_tool_is_rejected(self):
        token = self.manager.issue("remember", self.parameters)

        accepted, reason = self.manager.consume(
            token, "forget_memory", self.parameters
        )

        self.assertFalse(accepted)
        self.assertIn("authorize", reason.lower())

    def test_modified_parameters_are_rejected(self):
        token = self.manager.issue("remember", self.parameters)

        modified = dict(self.parameters)
        modified["value"] = "Different value"

        accepted, _ = self.manager.consume(
            token, "remember", modified
        )

        self.assertFalse(accepted)

    def test_expired_token_is_rejected(self):
        token = self.manager.issue("remember", self.parameters)

        self.current_time += 121

        accepted, reason = self.manager.consume(
            token, "remember", self.parameters
        )

        self.assertFalse(accepted)
        self.assertIn("expired", reason.lower())

    def test_cancelled_token_is_rejected(self):
        token = self.manager.issue("remember", self.parameters)

        self.manager.cancel(token)

        accepted, _ = self.manager.consume(
            token, "remember", self.parameters
        )

        self.assertFalse(accepted)

    def test_missing_token_is_rejected(self):
        accepted, _ = self.manager.consume(
            None, "remember", self.parameters
        )

        self.assertFalse(accepted)

    def test_invalid_token_is_rejected(self):
        accepted, reason = self.manager.consume(
            "non-existent-token", "remember", self.parameters
        )

        self.assertFalse(accepted)
        self.assertIn("invalid", reason.lower())

    def test_tokens_are_cryptographically_unpredictable(self):
        tokens = {
            self.manager.issue("remember", self.parameters)
            for _ in range(100)
        }
        self.assertEqual(len(tokens), 100)
        for token in tokens:
            self.assertGreaterEqual(len(token), 43)

    def test_token_at_exact_ttl_boundary_is_rejected(self):
        token = self.manager.issue("remember", self.parameters)
        self.current_time += 120.0

        accepted, reason = self.manager.consume(
            token, "remember", self.parameters
        )

        self.assertFalse(accepted)
        self.assertIn("expired", reason.lower())

    def test_empty_and_non_string_tokens_are_rejected(self):
        for bad_token in ("", 123, ["token"], {"token": "abc"}):
            accepted, reason = self.manager.consume(
                bad_token, "remember", self.parameters
            )
            self.assertFalse(accepted)
            self.assertIn("required", reason.lower())

    def test_failed_mismatch_burns_token_permanently(self):
        token = self.manager.issue("remember", self.parameters)

        modified = dict(self.parameters)
        modified["extra"] = "injected"

        accepted, _ = self.manager.consume(
            token, "remember", modified
        )
        self.assertFalse(accepted)

        # Replay with the original parameters must also fail.
        accepted_retry, _ = self.manager.consume(
            token, "remember", self.parameters
        )
        self.assertFalse(accepted_retry)

    def test_expired_grants_are_cleaned_up(self):
        self.manager.issue("remember", self.parameters)
        self.manager.issue("forget_memory", {"category": "a", "key": "b"})
        self.assertEqual(self.manager.pending_count, 2)

        self.current_time += 121.0
        fresh_token = self.manager.issue("write_file", {"path": "x", "content": "y"})

        self.assertEqual(self.manager.pending_count, 1)
        accepted, _ = self.manager.consume(
            fresh_token, "write_file", {"path": "x", "content": "y"}
        )
        self.assertTrue(accepted)
        self.assertEqual(self.manager.pending_count, 0)

    def test_grant_storage_is_bounded(self):
        bounded = ConfirmationManager(
            ttl_seconds=120,
            clock=lambda: self.current_time,
            max_grants=3,
        )
        tokens = [
            bounded.issue("remember", {"category": "c", "key": f"k{i}", "value": "v"})
            for i in range(5)
        ]

        self.assertEqual(bounded.pending_count, 3)

        # Oldest two grants were evicted; newest three remain valid.
        for i in range(2):
            accepted, _ = bounded.consume(
                tokens[i], "remember", {"category": "c", "key": f"k{i}", "value": "v"}
            )
            self.assertFalse(accepted)

        for i in range(2, 5):
            accepted, _ = bounded.consume(
                tokens[i], "remember", {"category": "c", "key": f"k{i}", "value": "v"}
            )
            self.assertTrue(accepted)

    def test_invalid_ttl_or_max_grants_rejected(self):
        with self.assertRaises(ValueError):
            ConfirmationManager(ttl_seconds=0)
        with self.assertRaises(ValueError):
            ConfirmationManager(ttl_seconds=-5)
        with self.assertRaises(ValueError):
            ConfirmationManager(max_grants=0)

    def test_concurrent_consumption_allows_only_one_winner(self):
        token = self.manager.issue("remember", self.parameters)
        threads_count = 8
        barrier = threading.Barrier(threads_count)
        results: list[bool] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            accepted, _ = self.manager.consume(
                token, "remember", self.parameters
            )
            with results_lock:
                results.append(accepted)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), threads_count)
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), threads_count - 1)


if __name__ == "__main__":
    unittest.main()

