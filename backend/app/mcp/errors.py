from __future__ import annotations


class McpError(Exception):
    def __init__(self, message: str, *, code: str = "mcp_error") -> None:
        super().__init__(message)
        self.code = code


class McpInvalidRequest(McpError):
    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(message, code="invalid_request")


class McpMethodNotFound(McpError):
    def __init__(self, message: str = "Method not found") -> None:
        super().__init__(message, code="method_not_found")


class McpNotFound(McpError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="resource_not_found")


class McpToolExecutionError(McpError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)
