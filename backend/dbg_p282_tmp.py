import asyncio, sys, tempfile
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, r"F:/work/buddy_work/2026-07-29-12-17-38/cyber-agent-platform/backend")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.acquisition.service import AcquisitionService
from app.acquisition.worker_path import AcquisitionWorkerPath
from app.acquisition.claim import AcquisitionClaimCoordinator
from app.evidence.service import EvidenceService
from app.sandbox import SandboxPolicyEngine, SandboxRuntime
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import MemorySandboxProvider
from app.worker.contracts import WorkerHeartbeat, WorkerRecord, WorkerStatus
from app.worker.lease import WorkerLeaseManager
from app.worker.plugin_runtime import PluginWorkerRuntime
from app.worker.registry import WorkerRegistry
from app.worker.runtime import WorkerRuntime
from app.worker.scheduler import WorkerScheduler
from tests.acquisition_lab import AcquisitionLabServer, lab_policy, lab_url_validator

async def main():
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "dbg.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    SF = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    lab = AcquisitionLabServer().start()
    lab.set_fail_page2(True)
    async with SF() as s:
        ev = EvidenceService(s, publisher=None, storage_directory=tmp)
        svc = AcquisitionService(s, ev, store_root=tmp / "o", policy=lab_policy(), validator=lab_url_validator())
        run, _ = await svc.create(goal="collect records", url=f"{lab.origin}/pagination?page=1", expected_fields=["title", "cve"], expected_record_count=30)
        await s.flush()
        reg = WorkerRegistry(s)
        w = await reg.register(WorkerRecord(name="w1", runtime_version="28.2", capabilities=frozenset({"acquisition.http"})))
        await reg.heartbeat(WorkerHeartbeat(worker_id=w.id, status=WorkerStatus.ONLINE, active_executions=0))
        leases = WorkerLeaseManager(s)
        provider = MemorySandboxProvider()
        rt = WorkerRuntime(s, reg, WorkerScheduler(reg), leases, SandboxRuntime(provider, SandboxPolicyEngine()))
        plugin = PluginWorkerRuntime(rt, SandboxProfile(name="acq"))
        coord = AcquisitionClaimCoordinator(s, leases, lease_ttl_seconds=60)
        wp = AcquisitionWorkerPath(plugin, svc, coord)
        token = uuid4()
        await coord.claim(run.id, w.id, token=token)
        task = asyncio.create_task(wp.run_claimed(run.id, w.id, token))
        await asyncio.sleep(0.6)
        async with SF() as cs:
            ev2 = EvidenceService(cs, publisher=None, storage_directory=tmp)
            svc2 = AcquisitionService(cs, ev2, store_root=tmp / "o", policy=lab_policy(), validator=lab_url_validator())
            l2 = WorkerLeaseManager(cs)
            c2 = AcquisitionClaimCoordinator(cs, l2, lease_ttl_seconds=60)
            rt2 = WorkerRuntime(cs, WorkerRegistry(cs), WorkerScheduler(WorkerRegistry(cs)), l2, SandboxRuntime(provider, SandboxPolicyEngine()))
            p2 = PluginWorkerRuntime(rt2, SandboxProfile(name="acq"))
            wp2 = AcquisitionWorkerPath(p2, svc2, c2)
            r2 = await svc2.get_run(run.id)
            print("sandbox_execution_id at cancel:", r2.sandbox_execution_id)
            if r2.sandbox_execution_id is not None:
                res = await p2.terminate(r2.sandbox_execution_id)
                print("terminate returned:", res)
            payload = await wp2.cancel(run.id)
            print("cancel payload status:", payload.status)
        result = await asyncio.wait_for(task, timeout=10)
        print("worker result:", result.status)
        await s.refresh(run)
        print("final run status:", run.status, "| stale_rejected:", run.stale_result_rejected)
    lab.stop()
    await engine.dispose()

asyncio.run(main())
