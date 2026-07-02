from __future__ import annotations

import json
import unittest

from app import api_settings_effective


class TestSettingsApi(unittest.TestCase):
    def test_effective_settings_do_not_expose_known_secrets(self):
        payload = api_settings_effective()
        encoded = json.dumps(payload)

        self.assertIn("configured", encoded)
        self.assertNotIn("8bf1cf8b-1940-4f6c-a06a-bfc37376ca9f", encoded)
        self.assertNotIn("0df656413b50d33d6f3ff56c1482106ca84d8cc042ae6e086fc0923bf678d420", encoded)


if __name__ == "__main__":
    unittest.main()
