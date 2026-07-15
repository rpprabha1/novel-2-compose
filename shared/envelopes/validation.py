"""Validation of Task Envelopes and Stage Responses against shared/schemas/.

The Coordinator calls validate_stage_response() on every response before accepting it
(CLAUDE.md rule: "validates every stage response against expected_output_schema").
This module only validates the envelope/response wrapper shape; validating a stage's
actual output payload against its expected_output_schema is a separate call using the
same underlying helper with a different schema file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from .models import StageResponse, TaskEnvelope

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


@lru_cache(maxsize=None)
def _load_schema(filename: str) -> dict:
    path = _SCHEMAS_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_task_envelope(envelope: TaskEnvelope) -> None:
    """Raises jsonschema.ValidationError if the envelope doesn't match task_envelope.schema.json."""
    jsonschema.validate(instance=envelope.to_dict(), schema=_load_schema("task_envelope.schema.json"))


def validate_stage_response(response: StageResponse) -> None:
    """Raises jsonschema.ValidationError if the response doesn't match stage_response.schema.json."""
    jsonschema.validate(instance=response.to_dict(), schema=_load_schema("stage_response.schema.json"))


def validate_against_schema(instance: dict, schema_filename: str) -> None:
    """Validate an arbitrary payload (e.g. a stage's beats.json) against a named schema
    file in shared/schemas/. Used by the Coordinator for expected_output_schema checks."""
    jsonschema.validate(instance=instance, schema=_load_schema(schema_filename))
