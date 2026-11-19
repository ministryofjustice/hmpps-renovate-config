import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=False
    )


@pytest.fixture
def image() -> str:
    if not shutil.which("docker") or not shutil.which("openssl"):
        pytest.skip("Docker and OpenSSL are required.")
    config = yaml.safe_load((ROOT / "compose.mirror-trust.yml").read_text())
    image: str = config["services"]["nexus-trust"]["image"]
    base = yaml.safe_load((ROOT / "compose.mirror.yml").read_text())
    assert image == base["services"]["nexus"]["image"]
    result = docker("image", "inspect", image)
    if result.returncode:
        pytest.skip("The Nexus image must already be available locally.")
    return image


def certificate(root: Path, name: str, *, ca: bool = True) -> tuple[Path, Path]:
    cert, key = root / f"{name}.crt", root / f"{name}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name}",
            "-addext",
            f"basicConstraints=critical,CA:{str(ca).upper()}",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        check=True,
    )
    return cert, key


def initialize(image: str, ca: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return docker(
        "run",
        "--rm",
        "--network=none",
        "--user",
        "0:0",
        "--mount",
        f"type=bind,src={ROOT / 'nexus-trust.sh'},dst=/gappy/nexus-trust.sh,readonly",
        "--mount",
        f"type=bind,src={ca},dst=/gappy/source-ca.crt,readonly",
        "--mount",
        f"type=bind,src={output},dst=/gappy-trust",
        "--entrypoint",
        "/bin/sh",
        image,
        "/gappy/nexus-trust.sh",
    )


def test_nexus_trust_preserves_roots_and_is_repeatable(tmp_path: Path, image: str) -> None:
    first, _ = certificate(tmp_path, "first")
    second, _ = certificate(tmp_path, "second")
    bundle = tmp_path / "bundle.crt"
    bundle.write_bytes(first.read_bytes() + second.read_bytes())
    output = tmp_path / "trust"
    output.mkdir()
    for _ in range(2):
        result = initialize(image, bundle, output)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "2 approved CA certificate(s)" in result.stdout
        assert not list(output.glob(".prepare.*"))
    default = docker(
        "run",
        "--rm",
        "--network=none",
        "--entrypoint",
        "keytool",
        image,
        "-list",
        "-cacerts",
        "-storepass",
        "changeit",
    )
    custom = docker(
        "run",
        "--rm",
        "--network=none",
        "--mount",
        f"type=bind,src={output},dst=/gappy-trust,readonly",
        "--entrypoint",
        "keytool",
        image,
        "-list",
        "-keystore",
        "/gappy-trust/cacerts",
        "-storepass",
        "changeit",
    )
    assert default.returncode == custom.returncode == 0, (default.stderr, custom.stderr)
    assert default.stdout.count("trustedCertEntry") > 0
    assert custom.stdout.count("trustedCertEntry") == default.stdout.count("trustedCertEntry") + 2
    assert "gappy-approved-ca-1" in custom.stdout
    assert "gappy-approved-ca-2" in custom.stdout


@pytest.mark.parametrize("kind", ["private-key", "leaf", "malformed"])
def test_nexus_trust_rejects_invalid_input_without_replacing_store(
    tmp_path: Path, image: str, kind: str
) -> None:
    cert, key = certificate(tmp_path, "CA:TRUE" if kind == "leaf" else "test", ca=kind != "leaf")
    if kind == "private-key":
        cert.write_bytes(cert.read_bytes() + key.read_bytes())
    elif kind == "malformed":
        cert.write_text("-----BEGIN CERTIFICATE-----\ninvalid\n")
    output = tmp_path / "trust"
    output.mkdir()
    store = output / "cacerts"
    store.write_bytes(b"existing-store")
    result = initialize(image, cert, output)
    assert result.returncode != 0
    assert result.stderr
    assert store.read_bytes() == b"existing-store"
    assert not list(output.glob(".prepare.*"))
