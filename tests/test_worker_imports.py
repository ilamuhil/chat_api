"""RQ imports jobs in a fresh worker; API startup order must not be required."""

import subprocess
import sys
import unittest
from pathlib import Path


class ColdWorkerImports(unittest.TestCase):
    def test_background_entrypoints_import_without_api_bootstrap(self):
        for module in (
            "app.services.lead_summary",
            "app.services.cron_jobs.conversations",
            "app.services.conversation_lifecycle",
            "app.services.billing",
            "app.services.latency",
        ):
            with self.subTest(module=module):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        f"import importlib; importlib.import_module({module!r})",
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-4000:])
