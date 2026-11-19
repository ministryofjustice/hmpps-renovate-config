"""Versioned evidence for offline four-way forecasts."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from gappy.contracts import Contract, Hash, Provider, Scan, timestamp


class Window(Contract):
    start: str
    end: str
    invocationMinutes: int = Field(default=60, ge=1, le=1440)

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: str) -> str:
        timestamp(value)
        return value

    @model_validator(mode="after")
    def bounded(self) -> Self:
        seconds = (timestamp(self.end) - timestamp(self.start)).total_seconds()
        if seconds <= 0 or seconds / (self.invocationMinutes * 60) > 10000:
            raise ValueError("Window must be positive and contain at most 10000 invocations.")
        return self


class PullRequest(Contract):
    number: int = Field(gt=0)
    provider: Provider
    branch: str
    base: str
    state: Literal["open", "closed"]
    createdAt: str

    @field_validator("createdAt")
    @classmethod
    def aware(cls, value: str) -> str:
        timestamp(value)
        return value


class PlatformState(Contract):
    repository: str
    capturedAt: str
    baseBranch: str
    complete: bool
    pullRequests: list[PullRequest]
    warnings: list[str]

    @field_validator("capturedAt")
    @classmethod
    def aware(cls, value: str) -> str:
        timestamp(value)
        return value

    @model_validator(mode="after")
    def unambiguous(self) -> Self:
        numbers = [pr.number for pr in self.pullRequests]
        if len(set(numbers)) != len(numbers):
            raise ValueError("Duplicate PR numbers in platform snapshot.")
        if self.complete and not self.baseBranch:
            raise ValueError("Complete platform state requires a base branch.")
        if not self.complete and not self.warnings:
            raise ValueError("Incomplete platform state requires a diagnostic.")
        return self


class Member(Contract):
    package: str = Field(min_length=1)
    target: str = Field(min_length=1)
    manifest: str


class Proposal(Contract):
    id: Hash
    ecosystem: str
    directory: str
    branch: str | None = None
    members: list[Member] = Field(min_length=1)
    existingUpdate: bool = False


class Policy(Contract):
    """Only proven policy inputs; unresolved native behavior stays explicit."""

    ecosystem: str
    directory: str
    interval: str = "always"
    day: str = "monday"
    time: str = "00:00"
    timezone: str = "UTC"
    openLimit: int = Field(ge=0)
    hourlyLimit: int | None = Field(default=None, ge=0)
    unknown: list[str] = Field(default_factory=list)


class Variant(Contract):
    label: Literal["baseline", "optimised"]
    provider: Provider
    configOrigin: str
    configHash: Hash
    scan: Scan
    proposals: list[Proposal]
    policies: list[Policy]
    proposalEvidenceComplete: bool
    warnings: list[str]

    @model_validator(mode="after")
    def matching_provider(self) -> Self:
        if self.scan.provider != self.provider:
            raise ValueError("Variant provider must match scan.")
        if len({p.id for p in self.proposals}) != len(self.proposals):
            raise ValueError("Duplicate proposal identities.")
        scopes = [(p.ecosystem, p.directory) for p in self.policies]
        if len(set(scopes)) != len(scopes):
            raise ValueError("Duplicate policy scopes cannot be forecast unambiguously.")
        return self


class ForecastInput(Contract):
    schemaVersion: Literal["1.0.0"] = "1.0.0"
    repository: str
    revision: str
    sourceHash: Hash
    window: Window
    platform: PlatformState
    variants: list[Variant]

    @model_validator(mode="after")
    def aligned(self) -> Self:
        expected = {(p, v) for p in ("dependabot", "renovate") for v in ("baseline", "optimised")}
        if len(self.variants) != 4 or {(v.provider, v.label) for v in self.variants} != expected:
            raise ValueError("Forecast requires exactly four provider/configuration variants.")
        if self.platform.repository != self.repository:
            raise ValueError("Platform state belongs to another repository.")
        if timestamp(self.window.start) < timestamp(self.platform.capturedAt):
            raise ValueError("Forecast window cannot begin before GitHub state capture.")
        for variant in self.variants:
            if (
                variant.scan.repository != self.repository
                or variant.scan.snapshot.revision != self.revision
            ):
                raise ValueError("Variants must refer to the same repository revision.")
            if timestamp(variant.scan.finishedAt) > timestamp(self.window.start):
                raise ValueError("Forecast window must start after native evidence collection.")
        return self
