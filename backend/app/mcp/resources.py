from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .adapters import McpServiceAdapter
from .errors import McpInvalidRequest, McpNotFound


@dataclass(frozen=True)
class McpResource:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"

    def definition(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


RESOURCES: tuple[McpResource, ...] = (
    McpResource(
        uri="rag://documents",
        name="documents",
        description="Safe list of active logical documents.",
    ),
)

RESOURCE_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "uriTemplate": "rag://documents/{logical_document_id}",
        "name": "document_status",
        "description": "Safe document detail and version status.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "rag://jobs/{job_id}",
        "name": "job_status",
        "description": "Safe job status and redacted payload summary.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "rag://evaluations/{evaluation_run_id}",
        "name": "evaluation_result",
        "description": "Safe evaluation run result and metrics.",
        "mimeType": "application/json",
    },
)


def list_resources() -> dict[str, Any]:
    return {"resources": [resource.definition() for resource in RESOURCES]}


def list_resource_templates() -> dict[str, Any]:
    return {"resourceTemplates": list(RESOURCE_TEMPLATES)}


def read_resource(adapter: McpServiceAdapter, uri: str) -> dict[str, Any]:
    data = _read_data(adapter, uri)
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
            }
        ]
    }


def _read_data(adapter: McpServiceAdapter, uri: str) -> dict[str, Any]:
    if uri == "rag://documents":
        return adapter.list_documents({})
    match = re.fullmatch(r"rag://documents/([1-9][0-9]*)", uri)
    if match:
        return adapter.get_document_status({"logical_document_id": int(match.group(1))})
    match = re.fullmatch(r"rag://jobs/([1-9][0-9]*)", uri)
    if match:
        return adapter.get_job_status({"job_id": int(match.group(1))})
    match = re.fullmatch(r"rag://evaluations/([1-9][0-9]*)", uri)
    if match:
        return adapter.get_evaluation_result({"evaluation_run_id": int(match.group(1))})
    if uri.startswith("rag://"):
        raise McpNotFound("resource not found")
    raise McpInvalidRequest("invalid resource uri")
