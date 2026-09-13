from __future__ import annotations

from sqlalchemy.orm import Session

from app.journey.models import SiteFormoVisitor
from app.models.order import Order, OrderStatus
from app.schemas.order import AnalysisHintsQ2V2, PackageContextQ2V2, Q2V2Payload
from app.services.q1_service import Q1OwnershipError, project_binding
from app.services.q2_scope_planner import legacy_q2_adapter, qualify_scope


def save_q2(db: Session, visitor: SiteFormoVisitor, order_id: str, payload: Q2V2Payload) -> tuple[bool, dict]:
    project_binding(db, visitor, order_id)
    order = db.get(Order, order_id)
    if order is None or order.status != OrderStatus.DRAFT:
        raise Q1OwnershipError("Only the current draft project may receive Q2")

    brief = dict(order.brief_answers or {})
    q1 = brief.get("q1_v2")
    if not isinstance(q1, dict):
        raise Q1OwnershipError("Q1 V2 must be saved on the same project before Q2")

    browsing = q1.get("package_browsing_context")
    starting = browsing.get("package_key") if isinstance(browsing, dict) else None
    if starting not in {"starter", "business", "reference", "advanced"}:
        starting = None
    existing = q1.get("existing_website")
    analysis = existing.get("analysis") if isinstance(existing, dict) else None
    data = analysis.get("data") if isinstance(analysis, dict) else None
    if (
        isinstance(analysis, dict)
        and analysis.get("source") == "existing_website"
        and analysis.get("status") == "unconfirmed"
        and isinstance(data, dict)
    ):
        def hints(key: str) -> list[str]:
            values = data.get(key)
            return [str(value)[:240] for value in values[:20]] if isinstance(values, list) else []
        analysis_hints = AnalysisHintsQ2V2(
            source="existing_website",
            status="unconfirmed",
            business_hints=hints("business_hints"),
            function_hints=hints("function_hints"),
            complexity_hints=hints("complexity_hints"),
        )
    else:
        analysis_hints = AnalysisHintsQ2V2()
    payload = payload.model_copy(update={
        "package_context": PackageContextQ2V2(
            starting_package=starting,
            source="package_browsing_context" if starting else "direct_entry",
        ),
        "analysis_hints": analysis_hints,
    })
    serialized = payload.model_dump(mode="json")
    qualification = qualify_scope(payload)
    stored = {**serialized, "scope_qualification": qualification}
    extended = dict(order.extended_brief or {})
    if extended.get("q2_v2") == stored:
        return True, qualification

    extended["q2_v2"] = stored
    extended["legacy_compatibility"] = legacy_q2_adapter(payload, qualification, q1)
    order.extended_brief = extended
    db.commit()
    return False, qualification
