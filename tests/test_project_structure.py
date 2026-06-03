import subprocess
from pathlib import Path

import pytest


def test_android_generated_files_are_not_tracked():
    """Generated Android build outputs and local SDK config must stay out of Git."""

    if not Path(".git").exists():
        pytest.skip("Git metadata is unavailable")

    tracked = subprocess.check_output(
        ["git", "ls-files", "client/android/app/build", "client/android/.gradle", "client/android/local.properties"],
        text=True,
    ).splitlines()

    assert tracked == []
