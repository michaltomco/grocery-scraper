"""Guard partial-refresh publishing and the final fail-visible workflow status."""
from pathlib import Path
import re
import unittest


class RefreshWorkflowTests(unittest.TestCase):
    def test_immutable_snapshot_is_post_commit_guarded_and_never_blocks_deploy(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/refresh-data.yml').read_text()
        steps = re.split(r'^      - ', workflow, flags=re.MULTILINE)[1:]
        snapshot = next(step for step in steps if 'id: snapshot\n' in step)
        upload = next(step for step in steps if 'name: nutrition-review-inputs\n' in step)
        for step in (snapshot, upload):
            for guard in ("steps.scrape.outputs.merged == 'true'", "steps.build.outcome == 'success'",
                          "steps.commit.outcome == 'success'"):
                self.assertIn(guard, step)
        self.assertIn("steps.snapshot.outcome == 'success'", upload)
        self.assertIn('snapshot --data-dir "$GITHUB_WORKSPACE" --out-dir nutrition-review-inputs', snapshot)
        self.assertLess(workflow.index('name: Commit refreshed data'), workflow.index('id: snapshot'))
        self.assertLess(workflow.index('name: nutrition-review-inputs'), workflow.index('name: Trigger website'))
        deploy = next(step for step in steps if 'name: Trigger website' in step)
        self.assertIn("always() && steps.commit.outcome == 'success'", deploy)
        self.assertNotIn('snapshot', deploy)

    def test_partial_refresh_can_publish_before_reporting_failure(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/refresh-data.yml").read_text()
        steps = re.split(r"^      - ", workflow, flags=re.MULTILINE)[1:]
        scrape = next((step for step in steps if "id: scrape\n" in step), "")
        self.assertIn("continue-on-error: true", scrape)
        self.assertIn("uv run python run.py", scrape)
        build = next(step for step in steps if "name: Build dashboard" in step)
        self.assertIn("steps.scrape.outputs.merged == 'true'", build)
        commit = next(step for step in steps if "name: Commit refreshed data" in step)
        self.assertIn("MERGED: ${{ steps.scrape.outputs.merged }}", commit)
        self.assertIn('if [ "$MERGED" = true ] && [ "$BUILD_OUTCOME" = success ]; then', commit)
        self.assertIn('git add refresh_status.json', commit)
        final = steps[-1]
        self.assertIn("steps.scrape.outcome == 'failure'", final)
        self.assertIn("always()", final)
        self.assertIn("exit 1", final)


if __name__ == "__main__":
    unittest.main()
