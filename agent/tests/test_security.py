from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_health_details_rejects_missing_proxy_key(self) -> None:
        response = self.client.get("/health/details")
        self.assertEqual(response.status_code, 401)

    def test_health_details_rejects_demo_role(self) -> None:
        response = self.client.get(
            "/health/details",
            headers={"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "demo"},
        )
        self.assertEqual(response.status_code, 403)

    def test_health_details_allows_owner_role(self) -> None:
        mocks = (
            patch("main.sec_status", return_value={}),
            patch("main.fred_status", return_value={}),
            patch("main.cfpb_status", return_value={}),
            patch("main.build_tool_registry", return_value=MagicMock(descriptions=lambda: [])),
            patch("main.long_term_memory", MagicMock(summary=lambda: {"backend": "local"})),
            patch("main.safety", MagicMock(status=lambda: {})),
            patch("main.audit", MagicMock(summary=lambda: {})),
        )
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5], mocks[6]:
            response = self.client.get(
                "/health/details",
                headers={"X-Varyn-Proxy-Key": "test-proxy-secret", "X-Varyn-Role": "owner"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("ok"))


class OwnerPathGatingTests(unittest.TestCase):
    """Direct unit coverage of is_owner_path(), independent of the TestClient
    route tests above -- verifies the owner-only prefix list itself, not the
    full middleware pipeline."""

    @staticmethod
    def make_request(path: str, query_string: bytes = b""):
        from fastapi import Request

        return Request(scope={"type": "http", "path": path, "query_string": query_string, "headers": []})

    def test_health_details_is_owner_gated(self) -> None:
        from security import is_owner_path

        self.assertTrue(is_owner_path(self.make_request("/health/details")))

    def test_health_remains_public_not_owner_gated(self) -> None:
        from security import is_owner_path

        self.assertFalse(is_owner_path(self.make_request("/health")))

    def test_audit_remains_owner_gated(self) -> None:
        from security import is_owner_path

        self.assertTrue(is_owner_path(self.make_request("/audit")))

    def test_confirmations_are_no_longer_blanket_owner_gated(self) -> None:
        # /confirmations/{id} is intentionally not owner-gated at the path level
        # since Mini Update: restore public risk-memo export -- main.py's
        # resolve_confirmation() route does its own per-confirmation,
        # action-aware owner check instead (confirmation_requires_owner()).
        from security import is_owner_path

        self.assertFalse(is_owner_path(self.make_request("/confirmations/confirm-abc123")))


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
