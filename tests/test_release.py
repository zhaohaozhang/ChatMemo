import tempfile
import unittest
import subprocess
from pathlib import Path

from release_check import scan_tree


class ReleaseHygieneTests(unittest.TestCase):
    def test_sensitive_artifacts_and_private_paths_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory.sqlite3").write_text("db", encoding="utf-8")
            (root / "model.gguf").write_text("model", encoding="utf-8")
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
            (root / "server.log").write_text("log", encoding="utf-8")
            (root / "real-chat-data").mkdir()
            (root / "real-chat-data" / "chat.txt").write_text("private", encoding="utf-8")
            (root / "config.json").write_text(
                r'{"model": "Z:\\Models\\private.gguf", "user": "C:\\Users\\ExampleUser"}',
                encoding="utf-8",
            )

            problems = scan_tree(root)
            joined = "\n".join(problems)
            for expected in (
                "memory.sqlite3",
                "model.gguf",
                ".env",
                "server.log",
                "real-chat-data",
                "absolute local path",
            ):
                self.assertIn(expected, joined)

    def test_gitignored_runtime_is_excluded_unless_forced_into_release_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("data/\nruntime/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
            (root / "data").mkdir()
            (root / "runtime").mkdir()
            (root / "data" / "memory.sqlite3").write_text("private", encoding="utf-8")
            (root / "runtime" / "llama.json").write_text("{}", encoding="utf-8")
            self.assertEqual([], scan_tree(root))

            subprocess.run(
                ["git", "add", "-f", "data/memory.sqlite3", "runtime/llama.json"],
                cwd=root,
                check=True,
            )
            joined = "\n".join(scan_tree(root))
            self.assertIn("data/memory.sqlite3", joined)
            self.assertIn("runtime/llama.json", joined)


if __name__ == "__main__":
    unittest.main()
