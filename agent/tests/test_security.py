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
from tools.registry import ToolRuntime, build_tool_registry


class RequestSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {"VARYN_PROXY_SECRET": "test-proxy-secret", "VARYN_SECURITY_REQUIRED": "true"},
        )
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.environment.stop()

    def test_public_health_is_sanitized(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "service": "varyn-agent", "status": "online"})

    def test_protected_route_rejects_direct_request(self) -> None:
        response = self.client.get("/config/public")
        self.assertEqual(response.status_code, 401)

    def test_demo_cannot_access_owner_route(self) -> None:
        response = self.client.get(
            "/audit",
            headers={"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "demo"},
        )
        self.assertEqual(response.status_code, 403)

    def test_owner_can_access_owner_route(self) -> None:
        response = self.client.get(
            "/audit",
            headers={"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "owner"},
        )
        self.assertEqual(response.status_code, 200)


class CapabilitySecurityTests(unittest.TestCase):
    def test_demo_cannot_run_owner_only_tool(self) -> None:
        result = build_tool_registry().run(
            "remember_fact",
            {"statement": "This must not be stored."},
            ToolRuntime(access_role="demo"),
        )
        self.assertFalse(result.ok)
        self.assertIn("Owner authentication", result.error or "")

    def test_upload_stops_before_oversized_file_is_committed(self) -> None:
        maximum = 10 * 1024 * 1024
        upload = UploadFile(filename="oversized.txt", file=io.BytesIO(b"x" * (maximum + 1)))
        with tempfile.TemporaryDirectory() as temporary:
            with patch("tools.files.UPLOAD_DIR", Path(temporary)):
                with self.assertRaises(UploadValidationError):
                    process_upload(upload, "test-session")
                self.assertFalse(any(Path(temporary).rglob("*.part")))
                self.assertFalse(any(Path(temporary).rglob("oversized.txt")))


if __name__ == "__main__":
    unittest.main()
