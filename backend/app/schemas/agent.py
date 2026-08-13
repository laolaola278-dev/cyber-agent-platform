"""Backward-compatible Agent schema aliases."""

from app.schemas.registry import AgentRegister, AgentRegistryRead

AgentCreate = AgentRegister
AgentRead = AgentRegistryRead

__all__ = ["AgentCreate", "AgentRead", "AgentRegister", "AgentRegistryRead"]
