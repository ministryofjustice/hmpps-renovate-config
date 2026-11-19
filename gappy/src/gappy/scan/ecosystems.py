"""Canonical comparison identities and official Dependabot updater names."""

# Public ecosystem -> native package manager, official image suffix.
DEPENDABOT = {
    name: (name.replace("-", "_"), name)
    for name in (
        "apm",
        "bazel",
        "bun",
        "bundler",
        "cargo",
        "composer",
        "conda",
        "deno",
        "devcontainers",
        "docker",
        "docker-compose",
        "dotnet-sdk",
        "elm",
        "github-actions",
        "gradle",
        "helm",
        "julia",
        "maven",
        "nix",
        "nuget",
        "pre-commit",
        "pub",
        "rust-toolchain",
        "sbt",
        "swift",
        "terraform",
        "opentofu",
        "uv",
        "vcpkg",
    )
}
DEPENDABOT.update(
    {
        "npm": ("npm_and_yarn", "npm"),
        "pip": ("pip", "pip"),
        "gomod": ("go_modules", "gomod"),
        "gitsubmodule": ("submodules", "gitsubmodule"),
        "mix": ("hex", "mix"),
    }
)

DATASOURCES = {
    "npm": "npm",
    "docker": "docker",
    "helm": "helm",
    "pypi": "pip",
    "maven": "maven",
    "nuget": "nuget",
    "go": "gomod",
    "rubygems": "bundler",
    "packagist": "composer",
    "crate": "cargo",
    "hex": "mix",
    "pub": "pub",
}
MANAGERS = {
    "github-actions": "github-actions",
    "pre-commit": "pre-commit",
    "helmv3": "helm",
    "helm-requirements": "helm",
    "helmfile": "helm",
    "dockerfile": "docker",
    "docker-compose": "docker",
    "helm-values": "docker",
    "devcontainer": "devcontainers",
    "git-submodules": "gitsubmodule",
    "pip_requirements": "pip",
    "pip_setup": "pip",
    "pipenv": "pip",
    "poetry": "pip",
    "pep621": "pip",
    "uv": "pip",
    "gradle": "maven",
    "sbt": "maven",
}


def ecosystem(manager: str, datasource: object) -> str:
    if manager == "github-actions" and datasource == "docker":
        return "docker"
    if manager in {"github-actions", "pre-commit", "git-submodules"}:
        return MANAGERS[manager]
    return DATASOURCES.get(str(datasource), MANAGERS.get(manager, manager))


def canonical_package(ecosystem: str, name: str) -> str:
    if ecosystem == "pip":
        import re

        return re.sub(r"[-_.]+", "-", name).lower()
    if ecosystem in {"github-actions", "pre-commit"}:
        return name.removeprefix("https://github.com/").removesuffix(".git").lower()
    if ecosystem == "docker":
        name = name.removeprefix("docker.io/").removeprefix("index.docker.io/")
        return name.removeprefix("library/")
    return name
