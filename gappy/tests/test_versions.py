import pytest

from gappy.versions import pinned_digest


@pytest.mark.parametrize(
    ("ecosystem", "value", "expected"),
    [
        ("github-actions", "a" * 40, "a" * 40),
        ("github-actions", "A" * 40, "a" * 40),
        ("github-actions", "a" * 7, None),
        ("github-actions", "v7.0.1", None),
        ("docker", "sha256:" + "a" * 64, "sha256:" + "a" * 64),
        ("docker", "24.0.0@sha256:" + "a" * 64, "sha256:" + "a" * 64),
        ("docker", "a" * 64, None),
        ("docker", "sha256:abc", None),
        ("docker", "24.0.0", None),
        ("npm", "a" * 40, None),
        ("helm", "sha256:" + "a" * 64, None),
        ("github-actions", None, None),
        ("docker", {}, None),
    ],
)
def test_pinned_digest_only_accepts_full_native_pin_formats(
    ecosystem: str, value: object, expected: str | None
) -> None:
    assert pinned_digest(ecosystem, value) == expected
