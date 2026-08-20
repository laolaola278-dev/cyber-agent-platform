"""Asynchronous SQLAlchemy engine and session factory."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield one database session per request.

    Phase 28.6: the DB is the durable queue's source of truth (API create =
    enqueue only). A session that is only flushed but never committed would
    be rolled back on close, so a successful request MUST commit -- otherwise
    every POST enqueues into a transaction that disappears, later reads (GET
    from another replica/session) 404, and concurrent identical idempotency
    keys all 'succeed' with distinct runs. On handler error the session is
    rolled back (no partial queue writes).
    """

    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
