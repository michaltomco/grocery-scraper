"""Guard partial-refresh publishing and the final fail-visible workflow status."""
from pathlib import Path
import re
import unittest


class RefreshWorkflowTests(unittest.TestCase):
    def test_partial_refresh_can_publish_before_reporting_failure(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/refresh-data.yml").read_text()
        steps = re.split(r"^      - ", workflow, flags=re.MULTILINE)[1:]
        scrape = next((step for step in steps if "id: scrape\n" in step), "")
        self.assertIn("continue-on-error: true", scrape)
        self.assertIn("uv run python run.py", scrape)
        build = next(step for step in steps if "name: Build dashboard" in step)
        self.assertIn("steps.scrape.outputs.merged == 'true'", build)
        commit = next(step for step in steps if "name: Commit refreshed data" in step)
        self.assertIn("steps.scrape.outputs.merged == 'true'", commit)
        final = steps[-1]
        self.assertIn("steps.scrape.outcome == 'failure'", final)
        self.assertIn("always()", final)
        self.assertIn("exit 1", final)


if __name__ == "__main__":
    unittest.main()
