"""Strict, versioned data exchanged between collection and offline comparison."""

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Provider = Literal["dependabot", "renovate"]
Policy = Literal["eligible", "excluded", "deferred", "unknown"]
Detection = Literal["detected", "not-detected", "unknown"]
Directness = Literal["direct", "transitive", "unknown"]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone.")
    return parsed


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Snapshot(Contract):
    revision: str
    contentHash: Hash
    configHashes: dict[str, Hash]


class Dependency(Contract):
    ecosystem: str
    package: str = Field(min_length=1)
    manifestPath: str
    installedVersion: str
    versioning: str = "semver"
    registry: str | None = None

    @field_validator("manifestPath")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
            raise ValueError("Manifest path must be a normalized relative path.")
        return value

    @field_validator("registry")
    @classmethod
    def safe_registry(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Registry identity must be a credential-free HTTP(S) URL.")
        return value

    def key(self) -> tuple[str, str, str, str, str, str | None]:
        return (
            self.ecosystem,
            self.package,
            self.manifestPath,
            self.installedVersion,
            self.versioning,
            self.registry,
        )


class Opportunity(Contract):
    dependency: Dependency
    directness: Directness = "unknown"
    candidateVersion: str
    availableAt: str

    @field_validator("availableAt")
    @classmethod
    def valid_time(cls, value: str) -> str:
        return timestamp(value).astimezone(UTC).isoformat()


class Provenance(Contract):
    source: str
    complete: bool
    warnings: list[str] = Field(default_factory=list)


class Inventory(Contract):
    schemaVersion: Literal["1.0.0", "1.1.0"]
    repository: str
    snapshotHash: Hash
    asOf: str
    provenance: Provenance
    opportunities: list[Opportunity]

    @model_validator(mode="after")
    def valid_inventory(self) -> Self:
        cutoff = timestamp(self.asOf)
        seen: dict[
            tuple[tuple[str, str, str, str, str, str | None], str], tuple[str, Directness]
        ] = {}
        for item in self.opportunities:
            if timestamp(item.availableAt) > cutoff:
                raise ValueError("Patch availability is later than inventory asOf.")
            key = (item.dependency.key(), item.candidateVersion)
            if key in seen:
                available_at, directness = seen[key]
                if timestamp(available_at) != timestamp(item.availableAt):
                    raise ValueError("Conflicting availability dates for one patch.")
                if directness != item.directness:
                    raise ValueError("Conflicting directness for one patch.")
            seen[key] = (item.availableAt, item.directness)
        return self


class Candidate(Contract):
    targetVersion: str
    evidenceId: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    targetPackage: str | None = None


class ConfigurationReference(Contract):
    id: str
    labels: list[str]
    basis: Literal["dependabot-entry", "renovate-resolved", "configured-scope"]


class Observation(Contract):
    dependency: Dependency
    extracted: bool = True
    directness: Directness = "unknown"
    policy: Policy
    reason: str
    lookupComplete: bool
    candidates: list[Candidate]
    configuration: list[ConfigurationReference] = Field(default_factory=list)


class Scan(Contract):
    schemaVersion: Literal["1.0.0", "1.1.0"]
    provider: Provider
    sessionId: str
    repository: str
    snapshot: Snapshot
    toolchain: dict[str, str]
    startedAt: str
    finishedAt: str
    cacheMode: Literal["fresh", "persistent"]
    cacheVolumes: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "failed", "cancelled", "unsupported"]
    observations: list[Observation]
    warnings: list[str]

    @model_validator(mode="after")
    def valid_run(self) -> Self:
        if timestamp(self.startedAt) > timestamp(self.finishedAt):
            raise ValueError("Scan finished before it started.")
        if self.status != "complete" and not self.warnings:
            raise ValueError("Non-successful scans require a diagnostic.")
        keys = [(item.dependency.key(), item.directness) for item in self.observations]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate contextual dependency observations.")
        for value in self.toolchain.values():
            if "@sha256:" in value and not re.fullmatch(r".+@sha256:[a-f0-9]{64}", value):
                raise ValueError("Invalid image digest.")
        return self
