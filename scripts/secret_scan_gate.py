from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import NoReturn

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPOSITORY_ROOT / ".gitleaks.toml"
IGNORE_PATH = REPOSITORY_ROOT / ".gitleaksignore"
EXPECTED_VERSION = "8.30.1"
EXPECTED_FINGERPRINTS = (
    "8c5c8473f8068436ec3b58d636ef3f9223afc797:"
    "backend/tests/test_graph_citations.py:generic-api-key:114",
    "d4c80c6597e71692b176451bafe4560dadafd0d8:docs/phase2/README.md:generic-api-key:283",
    "9a2db9dcffc0f08561b1cffe869ee71c709483c4:docs/phase2/README.md:generic-api-key:206",
)
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{40}:[^:*?\[\]\r\n]+:[a-z0-9][a-z0-9-]+:[1-9][0-9]*$")
DETECTION_EXIT_CODE = 93


class PolicyError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise PolicyError(message)


def validate_config(path: Path) -> None:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data != {"extend": {"useDefault": True}}:
        _fail("config must only extend the default Gitleaks rules")


def _ignore_entries(path: Path) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def validate_ignore(path: Path) -> None:
    entries = _ignore_entries(path)
    if entries != EXPECTED_FINGERPRINTS:
        _fail("ignore file must contain only the three reviewed historical fingerprints")
    if len(entries) > 3:
        _fail("ignore file exceeds the exact-fingerprint limit")
    if any(not FINGERPRINT_PATTERN.fullmatch(entry) for entry in entries):
        _fail("ignore entries must be commit:path:rule:line exact fingerprints")


def validate_policy() -> None:
    validate_config(CONFIG_PATH)
    validate_ignore(IGNORE_PATH)


def _expect_policy_rejection(action: object, label: str) -> None:
    try:
        action()  # type: ignore[operator]
    except PolicyError:
        print(f"POLICY_NEGATIVE_PASS case={label}")
        return
    _fail(f"policy parser accepted forbidden suppression: {label}")


def run_policy_negative_controls() -> None:
    with tempfile.TemporaryDirectory(prefix="rag-secret-policy-") as raw_temp:
        temp = Path(raw_temp)
        broad_ignore = temp / "broad.ignore"
        broad_ignore.write_text("generic-api-key\n", encoding="utf-8")
        _expect_policy_rejection(lambda: validate_ignore(broad_ignore), "rule_wide_ignore")

        wildcard_ignore = temp / "wildcard.ignore"
        wildcard_ignore.write_text("*:*:generic-api-key:*\n", encoding="utf-8")
        _expect_policy_rejection(lambda: validate_ignore(wildcard_ignore), "wildcard_ignore")

        broad_config = temp / "broad.toml"
        broad_config.write_text(
            "[extend]\nuseDefault = true\n\n[[allowlists]]\nregexes = ['.*']\n",
            encoding="utf-8",
        )
        _expect_policy_rejection(lambda: validate_config(broad_config), "broad_config_allowlist")


class Scanner:
    def __init__(self, *, executable: str | None, docker_image: str | None) -> None:
        if bool(executable) == bool(docker_image):
            _fail("select exactly one scanner transport")
        self.executable = executable
        self.docker_image = docker_image

    def check_version(self) -> None:
        if self.executable:
            command = [self.executable, "version"]
        else:
            command = ["docker", "run", "--rm", self.docker_image or "", "version"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        combined = result.stdout + result.stderr
        if result.returncode != 0 or EXPECTED_VERSION not in combined:
            _fail("scanner version does not match the repository pin")
        print(f"SCANNER_VERSION_OK version={EXPECTED_VERSION}")

    def scan(
        self,
        *,
        scan_kind: str,
        source: Path,
        ignore_path: Path = IGNORE_PATH,
        log_opts: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        source = source.resolve()
        ignore_path = ignore_path.resolve()
        common = [
            f"--config={CONFIG_PATH.resolve()}",
            f"--gitleaks-ignore-path={ignore_path}",
            "--redact=100",
            "--verbose",
            "--no-banner",
            "--no-color",
            f"--exit-code={DETECTION_EXIT_CODE}",
        ]
        if log_opts:
            common.append(f"--log-opts={log_opts}")
        if self.executable:
            command = [self.executable, scan_kind, *common, str(source)]
        else:
            container_common = [
                "--config=/policy/gitleaks.toml",
                "--gitleaks-ignore-path=/policy/gitleaksignore",
                "--redact=100",
                "--verbose",
                "--no-banner",
                "--no-color",
                f"--exit-code={DETECTION_EXIT_CODE}",
            ]
            if log_opts:
                container_common.append(f"--log-opts={log_opts}")
            command = [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{source}:/scan:ro",
                "--volume",
                f"{CONFIG_PATH.resolve()}:/policy/gitleaks.toml:ro",
                "--volume",
                f"{ignore_path}:/policy/gitleaksignore:ro",
                self.docker_image or "",
                scan_kind,
                *container_common,
                "/scan",
            ]
        return subprocess.run(command, capture_output=True, text=True, check=False)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def _initialize_repository(repository: Path) -> None:
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Secret Scan Negative Control")
    _git(repository, "config", "user.email", "secret-scan@example.invalid")


def _synthetic_mutation() -> tuple[str, str]:
    label = "".join(("api", "_key"))
    value = hashlib.sha256(b"rag-secret-scan-negative-control-v1").hexdigest()
    return f'{label} = "{value}"\n', value


def _assert_clean(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        _fail(f"expected clean scan failed: {label}")
    print(f"SCAN_CLEAN scope={label} unignored=0")


def _assert_detected(
    result: subprocess.CompletedProcess[str],
    *,
    label: str,
    matched_value: str | None = None,
    expected_metadata: str | None = None,
) -> None:
    combined = result.stdout + result.stderr
    if result.returncode != DETECTION_EXIT_CODE:
        _fail(f"negative control was not detected: {label}")
    if matched_value and matched_value in combined:
        _fail(f"redaction failed for negative control: {label}")
    if expected_metadata and expected_metadata not in combined:
        _fail(f"negative control metadata mismatch: {label}")
    print(f"NEGATIVE_CONTROL_PASS case={label} detected=1 redacted=1")


def run_negative_controls(scanner: Scanner) -> None:
    mutation, matched_value = _synthetic_mutation()
    with tempfile.TemporaryDirectory(prefix="rag-secret-new-file-") as raw_temp:
        repository = Path(raw_temp)
        _initialize_repository(repository)
        (repository / "negative_control.py").write_text(mutation, encoding="utf-8")
        _git(repository, "add", "negative_control.py")
        _git(repository, "commit", "--quiet", "-m", "negative control")
        result = scanner.scan(scan_kind="git", source=repository)
        _assert_detected(
            result,
            label="new_file_mutation",
            matched_value=matched_value,
            expected_metadata="generic-api-key",
        )

    with tempfile.TemporaryDirectory(prefix="rag-secret-same-path-") as raw_temp:
        repository = Path(raw_temp)
        target = repository / "backend" / "tests" / "test_graph_citations.py"
        target.parent.mkdir(parents=True)
        _initialize_repository(repository)
        target.write_text("# safe baseline\n", encoding="utf-8")
        _git(repository, "add", target.relative_to(repository).as_posix())
        _git(repository, "commit", "--quiet", "-m", "baseline")
        target.write_text("# safe baseline\n\n" + mutation, encoding="utf-8")
        _git(repository, "add", target.relative_to(repository).as_posix())
        _git(repository, "commit", "--quiet", "-m", "same path mutation")
        result = scanner.scan(scan_kind="git", source=repository)
        _assert_detected(
            result,
            label="same_path_new_commit_line",
            matched_value=matched_value,
            expected_metadata="backend/tests/test_graph_citations.py:generic-api-key",
        )

    with tempfile.TemporaryDirectory(prefix="rag-secret-ignore-removal-") as raw_temp:
        temp = Path(raw_temp)
        clone = temp / "repository"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--no-hardlinks",
                str(REPOSITORY_ROOT),
                str(clone),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        reduced_ignore = temp / ".gitleaksignore"
        reduced_ignore.write_text("\n".join(EXPECTED_FINGERPRINTS[1:]) + "\n", encoding="utf-8")
        historical_commit = EXPECTED_FINGERPRINTS[0].split(":", maxsplit=1)[0]
        result = scanner.scan(
            scan_kind="git",
            source=clone,
            ignore_path=reduced_ignore,
            log_opts=f"{historical_commit}^..{historical_commit}",
        )
        _assert_detected(
            result,
            label="removed_exact_historical_fingerprint",
            expected_metadata=EXPECTED_FINGERPRINTS[0],
        )


def verify(scanner: Scanner) -> None:
    validate_policy()
    run_policy_negative_controls()
    scanner.check_version()
    _assert_clean(scanner.scan(scan_kind="dir", source=REPOSITORY_ROOT), "current_tree")
    _assert_clean(scanner.scan(scan_kind="git", source=REPOSITORY_ROOT), "repository_history")
    run_negative_controls(scanner)
    print("SECRET_SCAN_GATE_PASS negative_detection=3/3 output_redacted=100")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the repository secret-scan gate")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("policy", help="validate exact-suppression policy")
    verify_parser = subparsers.add_parser("verify", help="run scans and negative controls")
    transport = verify_parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--gitleaks", help="path to a Gitleaks 8.30.1 binary")
    transport.add_argument("--docker-image", help="digest-pinned Gitleaks 8.30.1 image")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "policy":
            validate_policy()
            run_policy_negative_controls()
            print("SECRET_SCAN_POLICY_PASS exact_fingerprints=3 default_rules=extended")
            return 0
        verify(Scanner(executable=args.gitleaks, docker_image=args.docker_image))
        return 0
    except (PolicyError, OSError, subprocess.SubprocessError) as exc:
        print(f"SECRET_SCAN_GATE_FAIL reason={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
