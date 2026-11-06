import json
import unittest
from pathlib import Path

from tools.asset_sync.jsonpatch import patch


ROOT = Path(__file__).resolve().parents[1]


class JsonPatchTests(unittest.TestCase):
    def test_unchanged_document_is_byte_identical(self):
        path = ROOT / "src/endfield/assets/profile/medals.json"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(patch(text, json.loads(text)), text)

    def test_appending_a_map_entry_preserves_existing_text(self):
        path = ROOT / "src/endfield/assets/profile/avatar_icon.json"
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
        value["99"] = {"Icon": "/ui/ef/test.png"}
        output = patch(text, value)
        self.assertEqual(json.loads(output), value)
        self.assertIn('    "99": {\n        "Icon": "/ui/ef/test.png"\n    }\n}', output)
        self.assertTrue(output.startswith(text.splitlines()[0]))

    def test_changed_numeric_value_uses_new_spelling(self):
        text = '{\n    "Value": 3,\n    "Other": 1\n}'
        output = patch(text, {"Value": 5.0, "Other": 1})
        self.assertIn('"Value": 5.0', output)
        self.assertEqual(json.loads(output)["Value"], 5.0)

    def test_unchanged_number_keeps_original_spelling(self):
        text = '{\n    "Value": 15,\n    "Other": 15.0\n}'
        value = json.loads(text)
        self.assertEqual(patch(text, value), text)


if __name__ == "__main__":
    unittest.main()
