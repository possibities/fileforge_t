from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from utils import processing_runner


class TestProcessingRunnerCli(unittest.TestCase):
    def test_missing_database_url_returns_usage_error(self):
        with patch.dict(os.environ, {}, clear=True):
            rc = processing_runner.run(["--upload-batch-id", "1"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
