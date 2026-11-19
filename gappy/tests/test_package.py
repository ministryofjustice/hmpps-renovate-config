import subprocess
import sys
import zipfile
from pathlib import Path


def test_packaged_resource_access_outside_checkout(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.resources import files; "
            "assert files('gappy.report').joinpath('styles.css').read_text(); "
            "assert files('gappy.report').joinpath('bootstrap.min.css').read_text(); "
            "assert files('gappy.report').joinpath('bootstrap.LICENSE').read_text(); "
            "assert files('gappy.scan').joinpath('toolchain.json').read_text(); "
            "from gappy.cli import main; main(['--help'])",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "scan" in result.stdout
    assert "compare" in result.stdout


def test_wheel_resources_when_built() -> None:
    import pytest

    wheels = sorted((Path(__file__).parents[1] / "dist").glob("gappy-*.whl"))
    if not wheels:
        pytest.skip("Run uv build to inspect distribution assets.")
    with zipfile.ZipFile(wheels[-1]) as wheel:
        assert "gappy/report/styles.css" in wheel.namelist()
        assert "gappy/report/bootstrap.min.css" in wheel.namelist()
        assert "gappy/report/bootstrap.LICENSE" in wheel.namelist()
        assert "gappy/scan/toolchain.json" in wheel.namelist()
        entry_points = wheel.read("gappy-0.1.0.dist-info/entry_points.txt").decode()
        assert "gappy = gappy.cli:main" in entry_points
