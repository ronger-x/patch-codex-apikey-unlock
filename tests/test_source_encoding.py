import codecs
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEXT_FILES = (
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "README.md",
    "SKILL.md",
    "patch.py",
    "tests/test_patch.py",
    "tests/test_source_encoding.py",
)


class SourceEncodingTests(unittest.TestCase):
    def test_text_files_are_ascii_utf8_without_bom(self):
        for relative_path in TEXT_FILES:
            with self.subTest(path=relative_path):
                data = (REPOSITORY_ROOT / relative_path).read_bytes()

                self.assertFalse(
                    data.startswith(codecs.BOM_UTF8),
                    f"{relative_path} must not contain a UTF-8 BOM",
                )
                try:
                    text = data.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    self.fail(f"{relative_path} is not valid UTF-8: {exc}")

                self.assertNotIn(
                    "\r",
                    text,
                    f"{relative_path} must use LF line endings",
                )
                self.assertTrue(
                    text.endswith("\n"),
                    f"{relative_path} must end with a newline",
                )

                for line_number, line in enumerate(text.split("\n"), start=1):
                    for column, character in enumerate(line, start=1):
                        if ord(character) > 0x7F:
                            self.fail(
                                f"{relative_path}:{line_number}:{column} contains "
                                f"non-ASCII U+{ord(character):04X}"
                            )


if __name__ == "__main__":
    unittest.main()
