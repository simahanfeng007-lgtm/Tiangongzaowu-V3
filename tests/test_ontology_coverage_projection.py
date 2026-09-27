"""Keep the published coverage projection checked by the existing full CI."""
from pathlib import Path
import subprocess
import sys


def test_ontology_coverage_matches_current_dictionary_and_sources():
    root = Path(__file__).resolve().parents[1]
    checked = subprocess.run(
        [sys.executable, str(root / "scripts/build-ontology-coverage.py"), "--check"],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
