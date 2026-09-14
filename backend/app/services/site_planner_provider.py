from __future__ import annotations

from typing import Protocol

from app.schemas.site_planner import SitePlannerProviderRequest, SitePlannerProviderResult


class SitePlannerProviderException(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__("Site planner provider failed")


class SitePlannerProvider(Protocol):
    """Inference-only injected boundary; orchestration and authority stay outside adapters."""

    async def create_structured_candidate(
        self, request: SitePlannerProviderRequest,
    ) -> SitePlannerProviderResult:
        ...
