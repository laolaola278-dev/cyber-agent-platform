"""Smoke test Phase 27 hybrid API endpoints."""
import asyncio
import sys

sys.path.insert(0, ".")

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db_session
from app.main import create_app

HEADERS = {
    "X-CAP-User": "administrator",
    "X-CAP-Proxy-Secret": "change-me-proxy-secret",
}


async def main() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app()
    app.state.audit_session_factory = factory

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", headers=HEADERS
    ) as client:
        triage = await client.post(
            "/agent/hybrid/triage",
            json={
                "source": {
                    "id": "evt-1",
                    "title": "phishing campaign with credential dumping",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "entities": ["10.0.0.5"],
                    "evidence_refs": ["evidence:1"],
                    "techniques": ["T1566"],
                },
                "context": {"cvss": 9.8, "in_kev": True},
            },
        )
        body = triage.json()
        print("hybrid triage:", triage.status_code)
        print("  classification:", body.get("classification"))
        print("  severity:", body.get("severity", {}).get("severity"))
        print("  technique:", body.get("technique_mapping", {}).get("technique_id"))
        print("  facts:", body.get("fact_count"))
        print("  knowledge_hits:", len(body.get("knowledge_hits", [])))
        print("  confidence:", body.get("confidence", {}).get("confidence"))
        print("  chain_stages:", body.get("chain_stages"))
        print("  grounding agg:", body.get("grounding", {}).get("aggregate"))

        evaluation = await client.get("/agent/hybrid/evaluation")
        ev = evaluation.json()
        print("\nhybrid evaluation:", evaluation.status_code, "| scenarios:", ev.get("scenario_count"))
        for name, group in (ev.get("groups") or {}).items():
            print(f"  {name}: triage={group['triage_accuracy']} sev={group['severity_accuracy']}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
