"""Application bridge: Zeek Adapter -> Telemetry Runtime -> Detection Service."""

from uuid import uuid4

from app.schemas.telemetry import TelemetryPolicy
from app.telemetry import TelemetryPlanner, TelemetryRuntime
from app.tools.zeek import ZeekAdapter


class ZeekTelemetryBridge:
    """Execute the existing Telemetry lifecycle and return validated records."""

    def __init__(
        self,
        adapter: ZeekAdapter,
        planner: TelemetryPlanner,
        runtime: TelemetryRuntime,
        policy: TelemetryPolicy,
    ) -> None:
        self._adapter = adapter
        self._planner = planner
        self._runtime = runtime
        self._policy = policy

    async def collect(self, *, source_id: str, stream: str = "zeek") -> list[dict[str, object]]:
        """Collect only through the registered Zeek Telemetry Plugin."""

        self._adapter.require_source(source_id)
        telemetry_task_id = uuid4()
        task_id = uuid4()
        plan, context = self._planner.plan(
            telemetry_task_id=telemetry_task_id,
            task_id=task_id,
            trace_id=f"zeek-{telemetry_task_id}",
            plugin_name="zeek-telemetry",
            stream=stream,
            partition="0",
            consumer="cap-zeek-detection",
            policy=self._policy,
            input_data=({"data_source_id": source_id},),
        )
        result = await self._runtime.execute(plan, context)
        return [record.model_dump(mode="json") for record in result.records]
