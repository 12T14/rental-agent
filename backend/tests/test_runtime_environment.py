from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_private_env_is_loaded_before_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "RENTAL_DEMO_MODE=live\nMAP_PROVIDER=fake\n",
                encoding="utf-8",
            )
            script = """
import os
import sys
from pathlib import Path
from backend.agent_core import config
from backend.app.agent_runtime import _configure_runtime_environment

os.environ.pop("RENTAL_DEMO_MODE", None)
os.environ.pop("MAP_PROVIDER", None)
_configure_runtime_environment(Path(sys.argv[1]))
print(os.environ["RENTAL_DEMO_MODE"])
print(os.environ["MAP_PROVIDER"])
"""
            result = subprocess.run(
                [sys.executable, "-c", script, os.fspath(env_path)],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.stdout.splitlines(), ["live", "fake"])


if __name__ == "__main__":
    unittest.main()
