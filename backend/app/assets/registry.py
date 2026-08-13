"""Asset type and relationship policy registry."""

from app.core.enums import AssetRelationType, AssetType


class AssetRegistry:
    """Configuration-first policy boundary for supported asset graph semantics."""

    def __init__(self) -> None:
        self._types = frozenset(AssetType)
        self._relations = frozenset(AssetRelationType)

    def require_type(self, asset_type: AssetType) -> AssetType:
        if asset_type not in self._types:
            raise ValueError(f"Unsupported asset type: {asset_type}")
        return asset_type

    def require_relation(self, relation_type: AssetRelationType) -> AssetRelationType:
        if relation_type not in self._relations:
            raise ValueError(f"Unsupported asset relation: {relation_type}")
        return relation_type

    @property
    def types(self) -> frozenset[AssetType]:
        return self._types
