"""Audit persistence operations."""

from app.models import AuditLog
from app.repositories.base import SQLAlchemyRepository


class AuditRepository(SQLAlchemyRepository[AuditLog]):
    model = AuditLog
