from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


class TaskCreationUxCopyTests(unittest.TestCase):
    def test_web_launcher_renders_three_scope_options_and_prompt_examples(self) -> None:
        source = (REPO_ROOT / "frontend" / "src" / "App.tsx").read_text(
            encoding="utf-8"
        )

        for expected in [
            "Single file",
            "Related files",
            "Whole project",
            "What should Codex build or modify?",
            "Create project scaffold",
            "Build desktop app structure",
            "Implement authentication flow",
            "Refactor backend architecture",
            "Create REST API and models",
            "More files may appear in diff review",
        ]:
            self.assertIn(expected, source)

    def test_mobile_launcher_renders_three_scope_options_and_prompt_examples(self) -> None:
        source = (REPO_ROOT / "mobile" / "App.tsx").read_text(encoding="utf-8")

        for expected in [
            "Single file",
            "Related files",
            "Whole project",
            "What should Codex build or modify?",
            "Create project scaffold",
            "Build desktop app structure",
            "Implement authentication flow",
            "Refactor backend architecture",
            "Create REST API and models",
            "More files may appear in diff review",
        ]:
            self.assertIn(expected, source)


if __name__ == "__main__":
    unittest.main()
