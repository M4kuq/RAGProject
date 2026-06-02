from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s" / "local"

REQUIRED_FILES = [
    "namespace.yaml",
    "kustomization.yaml",
    "secret.template.yaml",
    "configmap.yaml",
    "upload-pvc.yaml",
    "postgres.yaml",
    "qdrant.yaml",
    "migration-jobs.yaml",
    "backend.yaml",
    "worker.yaml",
    "frontend.yaml",
]

FORBIDDEN_PATTERNS = [
    r"kind:\s*Ingress\b",
    r"\bEKS\b",
    r"\bTerraform\b",
    r"\bRDS\b",
    r"\bOIDC\b",
    r"amazonaws\.com",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bghp_[A-Za-z0-9_]{20,}\b",
    r"\bsk-[A-Za-z0-9]{20,}\b",
    r"BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY",
]


def main() -> int:
    for file_name in REQUIRED_FILES:
        require_file(file_name)

    combined = "\n".join(read(file_name) for file_name in REQUIRED_FILES)
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            raise AssertionError(f"k8s manifests contain forbidden pattern: {pattern}")

    assert_contains("kustomization.yaml", "secret.template.yaml")
    assert_contains("kustomization.yaml", "backend.yaml")
    assert_contains("kustomization.yaml", "worker.yaml")
    assert_contains("kustomization.yaml", "frontend.yaml")

    assert_contains("secret.template.yaml", "kind: Secret")
    assert_contains("secret.template.yaml", "stringData:")
    assert_contains("secret.template.yaml", "change-me")
    assert_not_contains("secret.template.yaml", "\ndata:")

    assert_contains("configmap.yaml", "EMBEDDING_PROVIDER: fake")
    assert_contains("configmap.yaml", "GENERATION_PROVIDER: fake")
    assert_contains("configmap.yaml", "RERANK_PROVIDER: fake")

    assert_resource("upload-pvc.yaml", "PersistentVolumeClaim", "upload-storage")
    assert_workload("postgres.yaml", kind="StatefulSet", name="postgres")
    assert_workload("qdrant.yaml", kind="StatefulSet", name="qdrant")
    assert_workload("backend.yaml", kind="Deployment", name="backend")
    assert_workload("worker.yaml", kind="Deployment", name="worker")
    assert_workload("frontend.yaml", kind="Deployment", name="frontend")

    assert_resource("postgres.yaml", "Service", "postgres")
    assert_resource("qdrant.yaml", "Service", "qdrant")
    assert_resource("backend.yaml", "Service", "backend")
    assert_resource("frontend.yaml", "Service", "frontend")

    assert_resource("migration-jobs.yaml", "Job", "ragproject-migrate")
    assert_resource("migration-jobs.yaml", "Job", "ragproject-seed")
    assert_contains("migration-jobs.yaml", "resources:")
    assert_contains("migration-jobs.yaml", "imagePullPolicy: Never")

    for file_name in (
        "backend.yaml",
        "worker.yaml",
        "frontend.yaml",
        "migration-jobs.yaml",
    ):
        assert_contains(file_name, "imagePullPolicy: Never")

    print("k8s local manifests validated")
    return 0


def require_file(file_name: str) -> None:
    path = K8S_DIR / file_name
    if not path.is_file():
        raise AssertionError(f"missing {path}")


def read(file_name: str) -> str:
    return (K8S_DIR / file_name).read_text(encoding="utf-8")


def assert_contains(file_name: str, expected: str) -> None:
    text = read(file_name)
    if expected not in text:
        raise AssertionError(f"{file_name} does not contain {expected!r}")


def assert_not_contains(file_name: str, unexpected: str) -> None:
    text = read(file_name)
    if unexpected in text:
        raise AssertionError(f"{file_name} contains {unexpected!r}")


def assert_resource(file_name: str, kind: str, name: str) -> None:
    text = read(file_name)
    pattern = rf"kind:\s*{re.escape(kind)}\b[\s\S]*?name:\s*{re.escape(name)}\b"
    if not re.search(pattern, text):
        raise AssertionError(f"{file_name} does not define {kind}/{name}")


def assert_workload(file_name: str, *, kind: str, name: str) -> None:
    assert_resource(file_name, kind, name)
    for expected in (
        "readinessProbe:",
        "livenessProbe:",
        "resources:",
        "requests:",
        "limits:",
    ):
        assert_contains(file_name, expected)


if __name__ == "__main__":
    raise SystemExit(main())
