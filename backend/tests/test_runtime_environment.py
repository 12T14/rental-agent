from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RuntimeEnvironmentTests(unittest.TestCase):
    def configured_modes(self, content: str = "", process_values: dict | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(content, encoding="utf-8")
            script = """
import json
import os
import sys
from pathlib import Path
from backend.agent_core import config
from backend.app.agent_runtime import _configure_runtime_environment

os.environ.pop("RENTAL_DEMO_MODE", None)
os.environ.pop("MAP_PROVIDER", None)
os.environ.update(json.loads(sys.argv[2]))
_configure_runtime_environment(Path(sys.argv[1]))
print(os.environ["RENTAL_DEMO_MODE"])
print(os.environ["MAP_PROVIDER"])
"""
            result = subprocess.run(
                [sys.executable, "-c", script, os.fspath(env_path), json.dumps(process_values or {})],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

        return result.stdout.splitlines()

    def test_private_env_is_loaded_before_defaults(self) -> None:
        self.assertEqual(
            self.configured_modes("RENTAL_DEMO_MODE=live\nMAP_PROVIDER=fake\n"),
            ["live", "fake"],
        )

    def test_unconfigured_application_uses_live_search_and_amap(self) -> None:
        self.assertEqual(self.configured_modes(), ["live", "amap"])

    def test_existing_offline_configuration_is_preserved(self) -> None:
        self.assertEqual(
            self.configured_modes("RENTAL_DEMO_MODE=offline\n"),
            ["offline", "fake"],
        )

    def test_explicit_map_provider_is_independent_of_listing_mode(self) -> None:
        self.assertEqual(
            self.configured_modes("RENTAL_DEMO_MODE=offline\nMAP_PROVIDER=amap\n"),
            ["offline", "amap"],
        )

    def test_process_configuration_has_priority_over_private_file(self) -> None:
        self.assertEqual(
            self.configured_modes("RENTAL_DEMO_MODE=offline\n", {"RENTAL_DEMO_MODE": "live"}),
            ["live", "amap"],
        )

    def test_install_template_defaults_to_live_search_without_mongodb(self) -> None:
        template = (PROJECT_ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
        values = {}
        for line in template.splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        self.assertEqual(values["RENTAL_DEMO_MODE"], "live")
        self.assertEqual(values["MAP_PROVIDER"], "amap")
        self.assertEqual(values["CHECKPOINT_BACKEND"], "memory")


if __name__ == "__main__":
    unittest.main()
