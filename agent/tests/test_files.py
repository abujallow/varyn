from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from fastapi.testclient import TestClient

from main import app
from tools.files import UploadValidationError, process_upload


class ExtensionRestrictionTests(unittest.TestCase):
    def test_disallowed_extension_rejected_before_any_write(self):
        upload = UploadFile(filename="malware.exe", file=io.BytesIO(b"fake binary"))
        with tempfile.TemporaryDirectory() as temporary:
            with patch("tools.files.UPLOAD_DIR", Path(temporary)):
                with self.assertRaises(UploadValidationError):
                    process_upload(upload, "test-session")
                self.assertEqual(list(Path(temporary).rglob("*")), [])

    def test_allowed_extension_is_accepted(self):
        upload = UploadFile(filename="notes.txt", file=io.BytesIO(b"hello world"))
        with tempfile.TemporaryDirectory() as temporary:
            with patch("tools.files.UPLOAD_DIR", Path(temporary)):
                result = process_upload(upload, "test-session")
                self.assertEqual(result["extension"], ".txt")
                self.assertTrue(result["ready"])


class ContentLengthPrecheckTests(unittest.TestCase):
    """Verifies the fast-fail 413 in security.py, distinct from the streaming
    enforcement inside process_upload -- this rejects before any auth check or
    body read, based purely on the declared Content-Length header."""

    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"VARYN_PROXY_SECRET": "test-proxy-secret", "VARYN_SECURITY_REQUIRED": "true"},
        )
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.environment.stop()

    def test_oversized_content_length_rejected_before_auth(self):
        maximum = 10 * 1024 * 1024
        oversized = maximum + (2 * 1024 * 1024)
        response = self.client.post(
            "/upload",
            headers={"Content-Length": str(oversized)},
            content=b"",
        )
        self.assertEqual(response.status_code, 413)

    def test_declared_length_within_slack_passes_precheck(self):
        # Within the 1MB slack allowance -- should proceed past the precheck to
        # the normal auth gate (401, since no proxy key supplied), not 413.
        maximum = 10 * 1024 * 1024
        response = self.client.post(
            "/upload",
            headers={"Content-Length": str(maximum)},
            content=b"",
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
