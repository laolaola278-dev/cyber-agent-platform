"""Database status CHECK constraint tests."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Agent
from tests.conftest import TestSessionFactory


async def test_agent_status_check_constraint_rejects_unknown_value() -> None:
    async with TestSessionFactory() as session:
        session.add(Agent(name="invalid-state", version="1", author="test", status="UNKNOWN"))
        with pytest.raises(IntegrityError):
            await session.commit()
