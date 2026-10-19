import unittest
from unittest.mock import patch

import app as dds


class StorageStatusTests(unittest.TestCase):
    def test_local_mode_reports_local_storage(self):
        with patch.object(dds, "HOSTED_MODE", False):
            status = dds.storage_status()
        self.assertEqual(status["storage_scope"], "local")
        self.assertTrue(status["persistent_storage_configured"])
        self.assertIn("computador", status["storage_label"].lower())

    def test_hosted_mode_without_a_mounted_volume_is_not_reported_as_persistent(self):
        with patch.object(dds, "HOSTED_MODE", True), patch.object(
            dds, "persistent_storage_available", return_value=False
        ):
            status = dds.storage_status()
        self.assertEqual(status["storage_scope"], "server")
        self.assertFalse(status["persistent_storage_configured"])
        self.assertIn("navegador", status["storage_label"].lower())

    def test_hosted_mode_with_a_mounted_volume_is_reported_as_persistent(self):
        with patch.object(dds, "HOSTED_MODE", True), patch.object(
            dds, "persistent_storage_available", return_value=True
        ):
            status = dds.storage_status()
        self.assertEqual(status["storage_scope"], "server")
        self.assertTrue(status["persistent_storage_configured"])
        self.assertIn("servidor", status["storage_label"].lower())


if __name__ == "__main__":
    unittest.main()
