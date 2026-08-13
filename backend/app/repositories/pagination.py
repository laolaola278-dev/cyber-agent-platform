"""Typed pagination result shared by repositories and services."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageResult[ItemT]:
    """One page of repository data and its total cardinality."""

    items: Sequence[ItemT]
    page: int
    page_size: int
    total: int
