"""Reserved isolation providers for later deployment phases."""

from app.sandbox.contracts import SandboxCommand, SandboxResult


class _ReservedSandbox:
    provider_name = "reserved"

    async def execute(self, command: SandboxCommand) -> SandboxResult:
        raise NotImplementedError(f"{self.provider_name} sandbox is reserved for a later phase")


class DockerSandbox(_ReservedSandbox):
    provider_name = "Docker"


class FirecrackerSandbox(_ReservedSandbox):
    provider_name = "Firecracker"


class RemoteWorkerSandbox(_ReservedSandbox):
    provider_name = "Remote worker"
