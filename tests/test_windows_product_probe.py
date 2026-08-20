"""Product-source probe parser contracts; no remote process is started."""

import json
import unittest

from packaging.pipeline.adapters.windows_ssh import parse_product_probe, PRODUCT_SCHEMA
from packaging.pipeline.core.errors import PipelineError


class WindowsProductProbeTests(unittest.TestCase):
    def test_product_probe_schema_and_fields_are_exact(self):
        payload = {
            "schema": PRODUCT_SCHEMA,
            "host_alias": "windows-direct",
            "product_repo": r"D:\tw\source\taijiAgentv1.0",
            "product_branch": "codex/windows-local",
            "product_commit": "a" * 40,
            "product_clean": True,
            "base_present": True,
            "expected_tip_present": True,
            "blockers": [],
        }
        self.assertEqual(parse_product_probe(json.dumps(payload)), payload)

    def test_product_probe_rejects_extra_or_missing_fields(self):
        payload = {
            "schema": PRODUCT_SCHEMA,
            "host_alias": "windows-direct",
            "product_repo": r"D:\tw\source\taijiAgentv1.0",
            "product_branch": "codex/windows-local",
            "product_commit": "a" * 40,
            "product_clean": True,
            "base_present": True,
            "expected_tip_present": True,
            "blockers": [],
        }
        payload["unexpected"] = True
        with self.assertRaises(PipelineError) as context:
            parse_product_probe(json.dumps(payload))
        self.assertEqual(context.exception.category, "PRODUCT_SOURCE_INVALID")


if __name__ == "__main__":
    unittest.main()
