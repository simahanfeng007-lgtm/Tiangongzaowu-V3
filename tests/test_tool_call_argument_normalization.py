from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from v3.gutong.gutong_ceng import GutongCeng


class ToolCallArgumentNormalizationTests(unittest.TestCase):
    def test_double_encoded_arguments_reach_required_field_validation_as_object(self) -> None:
        expected = {
            "action": "docx.create",
            "target": "about-mother.docx",
            "args": {"content": "A short essay."},
        }
        reply = json.dumps(
            {
                "name": "omni_body",
                # This is the exact wire-shape that previously became
                # {"value": "{...}"} and falsely failed on missing action.
                "arguments": json.dumps(json.dumps(expected, ensure_ascii=False), ensure_ascii=False),
            },
            ensure_ascii=False,
        )

        name, arguments = GutongCeng.jiexi_diaoyong(reply)

        self.assertEqual(name, "omni_body")
        self.assertEqual(arguments, expected)

    def test_plain_non_json_argument_remains_a_scalar_value(self) -> None:
        self.assertEqual(GutongCeng._json_arguments("not-json"), {"value": "not-json"})


if __name__ == "__main__":
    unittest.main()
