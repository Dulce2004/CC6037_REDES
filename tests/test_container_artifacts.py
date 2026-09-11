"""Portable static checks for the Pharmacy HTTP container artifacts."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.server.http import (  # noqa: E402
    PharmacyHTTPConfigurationError,
    PharmacyHTTPSettings,
)


class PharmacyContainerArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile_path = PROJECT_DIRECTORY / "Dockerfile"
        cls.dockerignore_path = PROJECT_DIRECTORY / ".dockerignore"
        cls.guide_path = PROJECT_DIRECTORY / "docs" / "container-cloud-run-guide.md"
        cls.dockerfile = cls.dockerfile_path.read_text(encoding="utf-8")
        cls.dockerignore = cls.dockerignore_path.read_text(encoding="utf-8")
        cls.guide = cls.guide_path.read_text(encoding="utf-8")

    def test_required_artifacts_exist(self) -> None:
        self.assertTrue(self.dockerfile_path.is_file())
        self.assertTrue(self.dockerignore_path.is_file())
        self.assertTrue(self.guide_path.is_file())

    def test_base_image_is_versioned_official_python_slim(self) -> None:
        first_instruction = next(
            line.strip()
            for line in self.dockerfile.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        self.assertEqual(first_instruction, "FROM python:3.12-slim")
        self.assertNotIn(":latest", first_instruction)

    def test_runtime_environment_and_command_match_cloud_run_contract(self) -> None:
        self.assertIn("PYTHONPATH=/app/src", self.dockerfile)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", self.dockerfile)
        self.assertIn("PYTHONUNBUFFERED=1", self.dockerfile)
        self.assertIn("HOST=0.0.0.0", self.dockerfile)
        self.assertIn("PORT=8080", self.dockerfile)
        self.assertIn("PHARMACY_MCP_DATABASE_PATH=/tmp/pharmacy/", self.dockerfile)
        self.assertIn("EXPOSE 8080", self.dockerfile)
        self.assertIn(
            'CMD ["python", "-B", "-m", "pharmacy_mcp.server.http"]',
            self.dockerfile,
        )

    def test_container_runs_as_a_dedicated_non_root_user(self) -> None:
        self.assertIn("useradd", self.dockerfile)
        self.assertRegex(self.dockerfile, r"(?m)^USER 10001:10001$")
        self.assertNotRegex(self.dockerfile, r"(?m)^USER\s+(?:root|0)(?::0)?$")

    def test_image_copies_only_pharmacy_http_runtime_components(self) -> None:
        copy_lines = [
            line.strip()
            for line in self.dockerfile.splitlines()
            if line.strip().startswith("COPY ")
        ]
        self.assertEqual(len(copy_lines), 4)
        combined = "\n".join(copy_lines)
        for required in (
            "src/pharmacy_mcp/__init__.py",
            "src/pharmacy_mcp/jsonrpc",
            "src/pharmacy_mcp/pharmacy",
            "src/pharmacy_mcp/server",
        ):
            self.assertIn(required, combined)
        for excluded in ("host", "client", "tests", "docs", "config", ".git"):
            self.assertNotRegex(combined, rf"(?:^|/)({re.escape(excluded)})(?:/|\s|$)")

    def test_image_installs_no_external_server_or_sdk(self) -> None:
        normalized = self.dockerfile.casefold()
        for forbidden in (
            "pip install",
            "apt-get",
            "mcp-server-git",
            "server-filesystem",
            "fastmcp",
            "nodejs",
            "node ",
            "npm",
            "npx",
            "uvx",
            "gemini",
            "anthropic",
        ):
            self.assertNotIn(forbidden, normalized)

    def test_dockerignore_excludes_local_and_sensitive_artifacts(self) -> None:
        required_patterns = {
            ".git/",
            ".gitignore",
            "docs/",
            "tests/",
            "runtime/",
            "*.pdf",
            "src/pharmacy_mcp/host/",
            "src/pharmacy_mcp/client/",
            "**/__pycache__/",
            "*.py[cod]",
            ".venv/",
            "*.sqlite3",
            "*.jsonl",
            "*.log",
            "node_modules/",
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
        }
        patterns = {
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue(required_patterns.issubset(patterns))

    def test_required_pharmacy_json_is_not_excluded(self) -> None:
        normalized = self.dockerignore.casefold()
        for name in (
            "branches.json",
            "medications.json",
            "inventory.json",
            "interactions.json",
        ):
            self.assertNotIn(name, normalized)
        self.assertNotIn("src/pharmacy_mcp/pharmacy/data", normalized)

    def test_container_contains_no_secret_or_personal_build_setting(self) -> None:
        self.assertNotRegex(
            self.dockerfile,
            r"(?im)^(?:ARG|ENV)\s+.*(?:TOKEN|API_KEY|SECRET)\s*=",
        )
        self.assertNotIn("Authorization", self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?i)[A-Z]:\\|/Users/|/home/[A-Za-z]")

    def test_public_bind_without_token_remains_rejected(self) -> None:
        with self.assertRaisesRegex(PharmacyHTTPConfigurationError, "Bearer token"):
            PharmacyHTTPSettings.from_environ(
                {
                    "HOST": "0.0.0.0",
                    "PORT": "8080",
                    "PHARMACY_MCP_DATABASE_PATH": "/tmp/pharmacy/pharmacy.sqlite3",
                }
            )

    def test_documentation_covers_safe_local_and_future_cloud_operation(self) -> None:
        normalized_guide = self.guide.casefold()
        required_phrases = (
            "docker build -t pharmacy-mcp-http:local .",
            "127.0.0.1:8080:8080",
            "request-based billing",
            "minimum instances",
            "maximum instances",
            "secret manager",
            "/mcp",
            "/health",
            "cloud sql",
            "ephemeral",
        )
        for phrase in required_phrases:
            self.assertIn(phrase, normalized_guide)


if __name__ == "__main__":
    unittest.main()
