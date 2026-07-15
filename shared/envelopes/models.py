"""Task Envelope / Stage Response dataclasses.

Field shapes are fixed by CLAUDE.md section 5 and shared/schemas/task_envelope.schema.json
and shared/schemas/stage_response.schema.json. Any field change here must be mirrored in
both schema files and SCHEMAS.md, plus an ARCHITECTURE.md change-log entry (CLAUDE.md rule 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    NEEDS_INPUT = "NEEDS_INPUT"
    FALLBACK_ROUTED = "FALLBACK_ROUTED"
    FAILED = "FAILED"


@dataclass
class TaskEnvelope:
    envelope_id: str
    run_id: str
    stage: str
    attempt: int
    input_manifest: list[str]
    run_config_ref: str
    expected_output_schema: str
    deadline_hint_s: int | None = None

    def to_dict(self) -> dict:
        d = {
            "envelope_id": self.envelope_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "attempt": self.attempt,
            "input_manifest": self.input_manifest,
            "run_config_ref": self.run_config_ref,
            "expected_output_schema": self.expected_output_schema,
        }
        if self.deadline_hint_s is not None:
            d["deadline_hint_s"] = self.deadline_hint_s
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskEnvelope":
        return cls(
            envelope_id=d["envelope_id"],
            run_id=d["run_id"],
            stage=d["stage"],
            attempt=d["attempt"],
            input_manifest=d["input_manifest"],
            run_config_ref=d["run_config_ref"],
            expected_output_schema=d["expected_output_schema"],
            deadline_hint_s=d.get("deadline_hint_s"),
        )


@dataclass
class NeedsInputItem:
    reason_code: str
    question: str
    options: list[str]
    context_ref: str | None = None

    def to_dict(self) -> dict:
        d = {"reason_code": self.reason_code, "question": self.question, "options": self.options}
        if self.context_ref is not None:
            d["context_ref"] = self.context_ref
        return d


@dataclass
class FallbackRoutedItem:
    item_id: str
    reason_code: str
    detail: str | None = None

    def to_dict(self) -> dict:
        d = {"item_id": self.item_id, "reason_code": self.reason_code}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass
class ErrorInfo:
    message: str
    diagnostics: str | None = None

    def to_dict(self) -> dict:
        d = {"message": self.message}
        if self.diagnostics is not None:
            d["diagnostics"] = self.diagnostics
        return d


@dataclass
class StageResponse:
    envelope_id: str
    run_id: str
    stage: str
    status: StageStatus
    summary: str | None = None
    output_manifest: list[str] = field(default_factory=list)
    needs_input: list[NeedsInputItem] = field(default_factory=list)
    fallback_routed: list[FallbackRoutedItem] = field(default_factory=list)
    error: ErrorInfo | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "envelope_id": self.envelope_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "status": self.status.value if isinstance(self.status, StageStatus) else self.status,
        }
        if self.summary is not None:
            d["summary"] = self.summary
        if self.output_manifest:
            d["output_manifest"] = self.output_manifest
        if self.needs_input:
            d["needs_input"] = [item.to_dict() for item in self.needs_input]
        if self.fallback_routed:
            d["fallback_routed"] = [item.to_dict() for item in self.fallback_routed]
        if self.error is not None:
            d["error"] = self.error.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StageResponse":
        return cls(
            envelope_id=d["envelope_id"],
            run_id=d["run_id"],
            stage=d["stage"],
            status=StageStatus(d["status"]),
            summary=d.get("summary"),
            output_manifest=d.get("output_manifest", []),
            needs_input=[NeedsInputItem(**item) for item in d.get("needs_input", [])],
            fallback_routed=[FallbackRoutedItem(**item) for item in d.get("fallback_routed", [])],
            error=ErrorInfo(**d["error"]) if d.get("error") else None,
        )
