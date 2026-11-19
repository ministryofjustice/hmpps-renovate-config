import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gappy.io import GappyError
from gappy.scan.mirror import (
    DEFAULT_HELM_UPSTREAM,
    apply_npm_mirror,
    apply_registry_mirrors,
    configure,
    main,
    registry_credentials,
)
from tests.test_dependabot import snapshot

URL = "http://host.docker.internal:8081/repository/npmjs/"


def test_mirror_rewrites_only_disposable_snapshot_and_fingerprints_transport(
    tmp_path: Path,
) -> None:
    (tmp_path / "renovate.json").write_text('{"packageRules":[]}')
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / ".npmrc").write_text("save-exact=true\nregistry=https://registry.npmjs.org/\n")
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text("{}")
    original = snapshot()
    result = apply_npm_mirror(tmp_path, original, URL)
    assert result.contentHash != original.contentHash
    assert result.revision == original.revision
    assert "gappy:npm-mirror" in result.configHashes
    assert "gappy:npm-mirror" not in original.configHashes
    assert (tmp_path / ".npmrc").read_text() == f"save-exact=true\nregistry={URL}\n"
    assert (nested / ".npmrc").read_text().strip() == f"registry={URL}"
    config = json.loads((tmp_path / "renovate.json").read_text())
    assert config["packageRules"][-1] == {"matchDatasources": ["npm"], "registryUrls": [URL]}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8081/repository/npmjs",
        "http://user:secret@example.test/npm",
        "file:///tmp/cache",
        "https://mirror.test/?token=secret",
        "http://mirror.test/\nunsafe",
    ],
)
def test_mirror_rejects_unsafe_or_container_local_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(GappyError, match="container-reachable"):
        apply_npm_mirror(tmp_path, snapshot(), url)


@pytest.mark.parametrize(
    "content",
    [
        "registry=https://private.example/\n",
        "@private:registry=https://private.example/\n",
    ],
)
def test_mirror_never_silently_redirects_private_packages(tmp_path: Path, content: str) -> None:
    (tmp_path / "renovate.json").write_text("{}")
    (tmp_path / ".npmrc").write_text(content)
    with pytest.raises(GappyError, match="private or scoped"):
        apply_npm_mirror(tmp_path, snapshot(), URL)
    assert (tmp_path / ".npmrc").read_text() == content
    assert (tmp_path / "renovate.json").read_text() == "{}"


@pytest.mark.parametrize(
    "config",
    [
        {"npmrc": "registry=https://private.test"},
        {"packageRules": [{"registryUrls": ["https://private.test"]}]},
        {"packageRules": "invalid"},
        [],
    ],
)
def test_mirror_rejects_ambiguous_config(tmp_path: Path, config: object) -> None:
    (tmp_path / "renovate.json").write_text(json.dumps(config))
    with pytest.raises(GappyError):
        apply_npm_mirror(tmp_path, snapshot(), URL)


def test_configure_creates_proxy_and_enables_anonymous_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.side_effect = [
        b"[]",
        b"",
        b"",
        b"",
        b"",
        b'["NexusAuthorizingRealm"]',
        b"",
        b"",
    ]
    monkeypatch.setattr("gappy.scan.mirror.urllib.request.build_opener", lambda *args: opener)
    configure("http://localhost:8081", "test-password")
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert [request.method for request in requests] == [
        "GET",
        "POST",
        "POST",
        "POST",
        "POST",
        "GET",
        "PUT",
        "PUT",
    ]
    proxy = json.loads(requests[1].data)
    assert proxy["proxy"]["remoteUrl"] == "https://registry.npmjs.org"
    assert proxy["name"] == "npmjs"
    assert [json.loads(request.data)["name"] for request in requests[1:5]] == [
        "npmjs",
        "pypi",
        "dockerhub",
        "helm",
    ]
    assert json.loads(requests[3].data)["docker"]["httpPort"] == 8082
    assert "DockerToken" in json.loads(requests[6].data)
    assert json.loads(requests[-1].data)["enabled"]


def test_configure_surfaces_http_failures_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock()
    opener.open.side_effect = urllib.error.HTTPError("", 401, "no", {}, None)
    monkeypatch.setattr("gappy.scan.mirror.urllib.request.build_opener", lambda *args: opener)
    with pytest.raises(GappyError, match="HTTP 401") as error:
        configure("http://localhost:8081", "secret-canary")
    assert "secret-canary" not in str(error.value)


def test_configure_is_idempotent_and_preserves_existing_realms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repositories = [
        {
            "name": name,
            "format": format_,
            "type": "proxy",
            "attributes": {
                "proxy": {"remoteUrl": upstream},
                **(
                    {"docker": {"httpPort": 8082, "forceBasicAuth": False}}
                    if format_ == "docker"
                    else {}
                ),
            },
        }
        for name, format_, upstream in (
            ("npmjs", "npm", "https://registry.npmjs.org"),
            ("pypi", "pypi", "https://pypi.org"),
            ("dockerhub", "docker", "https://registry-1.docker.io"),
            ("helm", "helm", DEFAULT_HELM_UPSTREAM),
        )
    ]
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.side_effect = [
        json.dumps(repositories).encode(),
        b'["ExistingRealm","DockerToken"]',
        b"",
    ]
    monkeypatch.setattr("gappy.scan.mirror.urllib.request.build_opener", lambda *args: opener)
    configure("http://localhost:8081", "test-password")
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert [request.method for request in requests] == ["GET", "GET", "PUT"]
    assert requests[-1].full_url.endswith("/security/anonymous")


def test_configure_never_overwrites_a_mismatched_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
        [
            {
                "name": "npmjs",
                "format": "npm",
                "type": "proxy",
                "attributes": {"proxy": {"remoteUrl": "https://private.test"}},
            },
        ]
    ).encode()
    monkeypatch.setattr("gappy.scan.mirror.urllib.request.build_opener", lambda *args: opener)
    with pytest.raises(GappyError, match="not the expected public proxy"):
        configure("http://localhost:8081", "test-password")
    assert opener.open.call_count == 1


@pytest.mark.parametrize("url", ["http://other-host:8081", "https://localhost:8081"])
def test_configure_refuses_non_loopback_admin_transport(url: str) -> None:
    with pytest.raises(GappyError, match="loopback"):
        configure(url, "password")


def test_setup_cli_recommends_tls_gateway_without_disclosing_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_mock = MagicMock()
    monkeypatch.setattr("gappy.scan.mirror.configure", configure_mock)
    monkeypatch.setattr("sys.argv", ["mirror", "--password-stdin"])
    monkeypatch.setattr("sys.stdin", io.StringIO("secret-canary\n"))
    main()
    configure_mock.assert_called_once_with(
        "http://localhost:8081", "secret-canary\n", helm_upstream=DEFAULT_HELM_UPSTREAM
    )
    output = capsys.readouterr().out
    assert "--npm-mirror https://host.docker.internal:8443/repository/npm-all/" in output
    assert "--registry-ca .gappy-mirror/ca.crt" in output
    assert "secret-canary" not in output


def test_extra_mirrors_fingerprint_routing_and_map_only_selected_helm_source(
    tmp_path: Path,
) -> None:
    import yaml

    (tmp_path / "renovate.json").write_text("{}")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    chart = {
        "dependencies": [
            {"name": "example", "repository": "https://charts.example/"},
            {"name": "private", "repository": "https://private.example"},
            {"name": "oci", "repository": "oci://ghcr.io/charts"},
        ]
    }
    (tmp_path / "Chart.yaml").write_text(yaml.safe_dump(chart))
    result = apply_registry_mirrors(
        tmp_path,
        snapshot(),
        pypi="https://mirror.test/pypi/simple",
        docker="https://mirror.test:8444",
        helm=("https://charts.example=https://mirror.test/helm",),
    )
    updated = yaml.safe_load((tmp_path / "Chart.yaml").read_text())
    assert updated["dependencies"][0]["repository"] == "https://mirror.test/helm"
    assert updated["dependencies"][1:] == chart["dependencies"][1:]
    rules = json.loads((tmp_path / "renovate.json").read_text())["packageRules"]
    assert rules[0] == {
        "matchDatasources": ["pypi"],
        "registryUrls": ["https://mirror.test/pypi/simple/"],
    }
    assert rules[1]["matchDatasources"] == ["docker"]
    assert rules[1]["registryUrls"] == ["https://mirror.test:8444"]
    assert all(f"gappy:{kind}-mirror" in result.configHashes for kind in ("pypi", "docker", "helm"))
    assert result.contentHash != snapshot().contentHash
    assert (tmp_path / "requirements.txt").read_text() == "requests==2.31.0\n"


@pytest.mark.parametrize(
    "filename,content",
    [
        ("requirements.txt", "--extra-index-url https://private.test/simple\n"),
        ("requirements.in", "-i https://private.test/simple\n"),
        ("pyproject.toml", '[[tool.poetry.source]]\nurl = "https://private.test"\n'),
        ("uv.toml", '[[index]]\nurl = "https://private.test"\n'),
        ("Pipfile", '[[source]]\nurl = "https://private.test"\n'),
    ],
)
def test_pypi_mirror_rejects_existing_index_before_writing(
    tmp_path: Path, filename: str, content: str
) -> None:
    (tmp_path / "renovate.json").write_text("{}")
    (tmp_path / filename).write_text(content)
    with pytest.raises(GappyError, match="explicit Python"):
        apply_registry_mirrors(tmp_path, snapshot(), pypi="https://mirror.test/simple")
    assert (tmp_path / "renovate.json").read_text() == "{}"


@pytest.mark.parametrize(
    "options",
    [
        {"docker": "http://mirror.test"},
        {"docker": "https://mirror.test/repository/docker"},
        {"helm": ("missing-separator",)},
        {"helm": ("https://charts.test=https://localhost:8443/helm",)},
    ],
)
def test_extra_mirror_validation(tmp_path: Path, options: dict) -> None:
    with pytest.raises(GappyError):
        apply_registry_mirrors(tmp_path, snapshot(), **options)


def test_mirror_credentials_only_replace_supported_public_base_registries() -> None:
    assert registry_credentials("pip", pypi="https://mirror.test/simple/") == [
        {"type": "python_index", "index-url": "https://mirror.test/simple/", "replaces-base": True}
    ]
    assert registry_credentials("docker", docker="https://mirror.test:8444") == [
        {"type": "docker_registry", "registry": "mirror.test:8444", "replaces-base": True}
    ]
    assert registry_credentials("github-actions", pypi="https://mirror.test/simple/") == []


def test_npm_and_extra_mirror_overrides_can_be_combined(tmp_path: Path) -> None:
    (tmp_path / "renovate.json").write_text("{}")
    (tmp_path / "package.json").write_text("{}")
    initial = apply_npm_mirror(tmp_path, snapshot(), URL)
    result = apply_registry_mirrors(
        tmp_path,
        initial,
        pypi="https://mirror.test/simple",
        docker="https://mirror.test:8444",
    )
    assert all(f"gappy:{kind}-mirror" in result.configHashes for kind in ("npm", "pypi", "docker"))
    assert [
        rule["matchDatasources"]
        for rule in json.loads((tmp_path / "renovate.json").read_text())["packageRules"]
    ] == [
        ["npm"],
        ["pypi"],
        ["docker"],
    ]
