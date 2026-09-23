"""JSON Schema, generated from the models (#644).

Generated, never hand-written. A hand-maintained schema drifts from the
models it claims to describe, and the drift is invisible until something
validates as fine and then fails at the node.
"""
from __future__ import annotations

import json
from typing import Any

from .models import AnalysisSpec

SCHEMA_ID = "https://analyse.pdhc.se/schema/analysis-spec-v1.json"


def json_schema() -> dict[str, Any]:
    schema = AnalysisSpec.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "PDHC analysis spec, version 1"
    return schema


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"
