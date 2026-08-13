"""Governed Nuclei CLI adapter using an injected sandbox provider."""

import json
import math
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from app.exceptions import AssessmentExecutionError, AssessmentPolicyViolation
from app.sandbox import SandboxCommand, SandboxProvider
from app.schemas.assessment import AssessmentPolicy
from app.tools.nuclei.contracts import (
    ApprovedNucleiTemplate,
    NucleiExecutionRequest,
    NucleiExecutionResult,
)


class NucleiAdapter:
    """Build safe CLI arguments, execute Nuclei, and parse JSONL findings."""

    def __init__(
        self,
        sandbox: SandboxProvider,
        *,
        executable: str,
        template_root: Path,
        approved_templates: dict[str, ApprovedNucleiTemplate],
        max_output_bytes: int = 5_000_000,
    ) -> None:
        self._sandbox = sandbox
        self._executable = executable
        self._template_root = template_root.resolve()
        self._approved = dict(approved_templates)
        self._max_output_bytes = max_output_bytes

    async def execute(self, request: NucleiExecutionRequest) -> NucleiExecutionResult:
        target = self._validate_target(request.target)
        templates = self._resolve_templates(request.templates)
        request_budget = sum(item.max_requests for item in templates)
        if request_budget > request.policy.max_requests:
            raise AssessmentPolicyViolation(
                "Approved templates exceed the assessment request budget",
                details={"required": request_budget, "allowed": request.policy.max_requests},
            )
        arguments = self.build_arguments(target, templates, request.policy)
        result = await self._sandbox.execute(
            SandboxCommand(
                executable=self._executable,
                arguments=arguments,
                working_directory=self._template_root,
                timeout_seconds=request.policy.timeout_seconds,
                max_output_bytes=self._max_output_bytes,
                environment={
                    "DISABLE_NUCLEI_TEMPLATES_PUBLIC_DOWNLOAD": "true",
                    "DISABLE_NUCLEI_TEMPLATES_GITHUB_DOWNLOAD": "true",
                    "DISABLE_NUCLEI_TEMPLATES_GITLAB_DOWNLOAD": "true",
                    "DISABLE_NUCLEI_TEMPLATES_AWS_DOWNLOAD": "true",
                    "DISABLE_NUCLEI_TEMPLATES_AZURE_DOWNLOAD": "true",
                    "ENABLE_CLOUD_UPLOAD": "false",
                },
            )
        )
        if result.output_truncated:
            raise AssessmentExecutionError("Nuclei output exceeded the sandbox limit")
        if result.exit_code != 0:
            raise AssessmentExecutionError(
                "Nuclei execution failed",
                details={"exit_code": result.exit_code, "stderr": result.stderr},
            )
        return NucleiExecutionResult(
            records=tuple(self.parse_jsonl(result.stdout)),
            request_budget=request_budget,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
        )

    def build_arguments(
        self,
        target: str,
        templates: tuple[ApprovedNucleiTemplate, ...],
        policy: AssessmentPolicy,
    ) -> tuple[str, ...]:
        arguments: list[str] = [
            "-u",
            target,
            "-jsonl",
            "-silent",
            "-nc",
            "-duc",
            "-ni",
            "-no-stdin",
            "-no-httpx",
            "-or",
            "-ot",
            "-rl",
            str(max(1, math.floor(policy.rate_limit_per_second))),
            "-c",
            str(policy.max_concurrency),
            "-bs",
            "1",
            "-timeout",
            str(policy.timeout_seconds),
            "-retries",
            "0",
        ]
        for template in templates:
            arguments.extend(("-t", str(template.path)))
        return tuple(arguments)

    @staticmethod
    def parse_jsonl(output: str) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for line_number, line in enumerate(output.splitlines(), start=1):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError as error:
                raise AssessmentExecutionError(
                    "Nuclei returned invalid JSONL", details={"line": line_number}
                ) from error
            if not isinstance(value, dict):
                raise AssessmentExecutionError(
                    "Nuclei JSONL record must be an object", details={"line": line_number}
                )
            records.append(value)
        return records

    def _resolve_templates(self, requested: tuple[str, ...]) -> tuple[ApprovedNucleiTemplate, ...]:
        if not requested:
            raise AssessmentPolicyViolation("At least one approved Nuclei template is required")
        resolved: list[ApprovedNucleiTemplate] = []
        for template_id in dict.fromkeys(requested):
            try:
                template = self._approved[template_id]
            except KeyError as error:
                raise AssessmentPolicyViolation(
                    "Nuclei template is not approved", details={"template_id": template_id}
                ) from error
            path = template.path.resolve()
            if not path.is_relative_to(self._template_root) or not path.is_file():
                raise AssessmentPolicyViolation("Approved Nuclei template path is invalid")
            digest = sha256(path.read_bytes()).hexdigest()
            if digest != template.sha256:
                raise AssessmentPolicyViolation(
                    "Approved Nuclei template integrity check failed",
                    details={"template_id": template_id},
                )
            resolved.append(template)
        return tuple(resolved)

    @staticmethod
    def _validate_target(target: str) -> str:
        value = target.strip()
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AssessmentPolicyViolation("Nuclei target must be one HTTP(S) asset")
        if any(character in value for character in {"\n", "\r", "\x00", ","}):
            raise AssessmentPolicyViolation("Nuclei target contains forbidden characters")
        return value
