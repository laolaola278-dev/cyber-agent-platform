"""Zeek-specific convenience API over Telemetry and Detection Frameworks."""

from fastapi import APIRouter, Request, status

from app.dependencies import (
    DetectionServiceDependency,
    ZeekAdapterDependency,
    ZeekTelemetryBridgeDependency,
)
from app.schemas import DetectionTaskCreate, ZeekDetectionCreate, ZeekDetectionRead, ZeekStatusRead

router = APIRouter(prefix="/detection/zeek", tags=["detection", "zeek"])


@router.post("", response_model=ZeekDetectionRead, status_code=status.HTTP_201_CREATED)
async def ingest_zeek(
    payload: ZeekDetectionCreate,
    request: Request,
    service: DetectionServiceDependency,
    bridge: ZeekTelemetryBridgeDependency,
) -> ZeekDetectionRead:
    telemetry_records = await bridge.collect(source_id=payload.data_source_id)
    task = await service.create(
        DetectionTaskCreate(
            name=payload.name,
            asset_id=payload.asset_id,
            capabilities=[
                "network.detect",
                "log.detect",
                "traffic.detect",
                "event.detect",
                "ioc.detect",
                "rule.detect",
            ],
            log_source="zeek-telemetry",
            parser="zeek-jsonl",
            plugin_name="zeek-detection",
            input={
                "data_source_id": payload.data_source_id,
                "telemetry_records": telemetry_records,
            },
            execute=payload.execute,
        ),
        trace_id=request.state.request_id,
    )
    return ZeekDetectionRead.model_validate(task)


@router.get("/status", response_model=ZeekStatusRead)
async def zeek_status(adapter: ZeekAdapterDependency) -> ZeekStatusRead:
    return ZeekStatusRead.model_validate(adapter.status())
