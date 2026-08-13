"""Suricata-specific convenience API over the unchanged Detection Framework."""

from fastapi import APIRouter, Request, status

from app.dependencies import DetectionServiceDependency, SuricataAdapterDependency
from app.schemas import (
    DetectionTaskCreate,
    SuricataDetectionCreate,
    SuricataDetectionRead,
    SuricataStatusRead,
)

router = APIRouter(prefix="/detection/suricata", tags=["detection", "suricata"])


@router.post("", response_model=SuricataDetectionRead, status_code=status.HTTP_201_CREATED)
async def ingest_suricata(
    payload: SuricataDetectionCreate,
    request: Request,
    service: DetectionServiceDependency,
) -> SuricataDetectionRead:
    task = await service.create(
        DetectionTaskCreate(
            name=payload.name,
            asset_id=payload.asset_id,
            capabilities=[
                "network.detect",
                "ids.detect",
                "traffic.detect",
                "event.detect",
                "ioc.detect",
                "rule.detect",
            ],
            log_source="suricata-eve",
            parser="eve-jsonl",
            plugin_name="suricata-detection",
            input={"data_source_id": payload.data_source_id},
            execute=payload.execute,
        ),
        trace_id=request.state.request_id,
    )
    return SuricataDetectionRead.model_validate(task)


@router.get("/status", response_model=SuricataStatusRead)
async def suricata_status(adapter: SuricataAdapterDependency) -> SuricataStatusRead:
    return SuricataStatusRead.model_validate(adapter.status())
