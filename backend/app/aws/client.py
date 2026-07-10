from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings


def create_aws_client(service_name: str, settings: Settings) -> Any:
    return boto3.client(
        service_name,
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=settings.aws_sdk_connect_timeout_seconds,
            read_timeout=settings.aws_sdk_read_timeout_seconds,
            retries={"mode": "standard", "total_max_attempts": settings.aws_sdk_max_attempts},
            user_agent_extra="ragproject",
        ),
    )


def aws_error_category(exc: Exception) -> str:
    code = _aws_error_code(exc).lower()
    name = type(exc).__name__.lower()
    if code in {
        "accessdenied",
        "accessdeniedexception",
        "expiredtoken",
        "invalidclienttokenid",
        "invalidsignatureexception",
        "signaturedoesnotmatch",
        "unrecognizedclientexception",
    }:
        return "auth"
    if code in {
        "servicequotaexceededexception",
        "throttling",
        "throttlingexception",
        "toomanyrequestsexception",
    }:
        return "rate_limited"
    if code in {"modeltimeoutexception", "requesttimeout", "requesttimeoutexception"}:
        return "timeout"
    if code in {"nosuchkey", "notfound", "resourcenotfoundexception"}:
        return "not_found"
    if code in {"validationexception", "invalidparameter", "invalidrequest"}:
        return "invalid_request"
    if "timeout" in name:
        return "timeout"
    if "connection" in name or "endpoint" in name:
        return "connection"
    return "service_error"


def bedrock_model_arn(model_id: str, region: str) -> str:
    if model_id.startswith("arn:"):
        return model_id
    if region.startswith("cn-"):
        partition = "aws-cn"
    elif region.startswith("us-gov-"):
        partition = "aws-us-gov"
    else:
        partition = "aws"
    return f"arn:{partition}:bedrock:{region}::foundation-model/{model_id}"


def _aws_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    if not isinstance(error, dict):
        return ""
    code = error.get("Code")
    return code if isinstance(code, str) else ""
