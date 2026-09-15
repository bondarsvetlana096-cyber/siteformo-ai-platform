"""Pure Generator V2 input and job contracts.

This module deliberately has no runtime-generation imports.  It binds an
implementation attempt to the already validated planning artifacts without
consulting an Order, database, queue, provider, or filesystem.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.generation_context import GenerationContextV1
from app.schemas.site_plan import SitePlanV1
from app.schemas.site_planner import PlannerConstraintProjectionV1
from app.services.site_planner_constraint_projection import build_planner_constraint_projection_v1
from app.services.site_plan_validator import validate_site_plan_v1


GENERATOR_INPUT_CONTRACT_VERSION = "v1"
IMPLEMENTATION_STRATEGY_VERSION = "v1"


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


AssetKind = Literal[
    "client_media", "approved_generated_image", "approved_siteformo_image",
    "video", "authorized_logo", "factual_source",
]
AssetAuthority = Literal[
    "generation_context", "client_approval", "owner_approval", "siteformo_library",
]
ContentKind = Literal[
    "confirmed_fact", "generated_copy_allowed", "client_material_required",
    "placeholder_allowed",
]


class AuthorizedAssetReferenceV1(ClosedModel):
    asset_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: AssetKind
    authority: AssetAuthority
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AuthorizedContentReferenceV1(ClosedModel):
    content_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: ContentKind
    source_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class GeneratorV2SelectedDesignIdentityV1(ClosedModel):
    design_direction: Literal[
        "clean-modern", "premium-business", "bold-startup", "luxury-elite",
        "tech-minimal", "creative-studio", "nordic-soft", "dark-contrast",
    ]
    design_direction_contract_version: Literal["v1"]
    interaction_preference: Literal["subtle", "recommended", "more_expressive"]
    interaction_preference_contract_version: Literal["v1"]
    visual_reference_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _site_plan_hash(plan: SitePlanV1, context_hash: str) -> str:
    material = plan.model_dump(mode="json", exclude={"site_plan_hash", "created_at", "plan_status"})
    material["generation_context_hash"] = context_hash
    return _sha(material)


def _site_plan_identity(plan: SitePlanV1) -> dict:
    """Return the plan fields that define implementation authority.

    ``created_at`` is observability metadata, not canonical identity.  Keeping
    it in the embedded snapshot while excluding it from identity hashing makes
    persistence/replay deterministic without losing the validated snapshot.
    """
    return plan.model_dump(mode="json", exclude={"created_at"})


class GeneratorV2InputSnapshotV1(ClosedModel):
    contract_version: Literal["v1"]
    order_id: str = Field(min_length=1, max_length=128)
    journey_project_id: str = Field(min_length=1, max_length=128)
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    constraint_projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    planner_operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_context_contract_version: Literal["v1"]
    constraint_projection_contract_version: Literal["v1"]
    site_plan_contract_version: Literal["v1"]
    planner_contract_version: Literal["v1"]
    policy_version: Literal["v1"]
    validator_version: Literal["v1"]
    selected_design: GeneratorV2SelectedDesignIdentityV1
    site_plan: SitePlanV1
    authorized_assets: tuple[AuthorizedAssetReferenceV1, ...] = ()
    authorized_content: tuple[AuthorizedContentReferenceV1, ...] = ()
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_hashes(self) -> "GeneratorV2InputSnapshotV1":
        if _site_plan_hash(self.site_plan, self.generation_context_hash) != self.site_plan_hash:
            raise ValueError("site_plan_hash_mismatch")
        material = self.model_dump(mode="json", exclude={"generator_input_hash", "site_plan"})
        material["site_plan"] = _site_plan_identity(self.site_plan)
        if _sha(material) != self.generator_input_hash:
            raise ValueError("generator_input_hash_mismatch")
        return self


SnapshotStatus = Literal["READY", "NOT_READY", "STALE_OR_MISMATCH"]
SnapshotReason = Literal[
    "site_plan_invalid", "site_plan_hash_mismatch", "context_hash_mismatch",
    "projection_hash_mismatch", "planner_operation_mismatch", "design_identity_mismatch",
    "interaction_identity_mismatch", "unauthorized_asset_reference",
    "unsupported_contract_version", "snapshot_hash_mismatch",
]


class GeneratorV2SnapshotResultV1(ClosedModel):
    status: SnapshotStatus
    reason_codes: tuple[SnapshotReason, ...] = ()
    snapshot: GeneratorV2InputSnapshotV1 | None = None


class GeneratorV2JobPayloadV1(ClosedModel):
    contract_version: Literal["v1"]
    order_id: str = Field(min_length=1, max_length=128)
    journey_project_id: str = Field(min_length=1, max_length=128)
    generator_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str | None = Field(default=None, max_length=128)
    site_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    constraint_projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_context_contract_version: Literal["v1"]
    constraint_projection_contract_version: Literal["v1"]
    site_plan_contract_version: Literal["v1"]
    implementation_strategy_version: Literal["v1"]
    approved_revision_version: str | None = Field(default=None, max_length=64)


def _build_material(
    *, order_id: str, journey_project_id: str, context_hash: str,
    projection: PlannerConstraintProjectionV1, plan: SitePlanV1,
    planner_operation_key: str, selected_design: GeneratorV2SelectedDesignIdentityV1,
    assets: tuple[AuthorizedAssetReferenceV1, ...],
    content: tuple[AuthorizedContentReferenceV1, ...],
) -> dict:
    return {
        "contract_version": GENERATOR_INPUT_CONTRACT_VERSION,
        "order_id": order_id,
        "journey_project_id": journey_project_id,
        "generation_context_hash": context_hash,
        "constraint_projection_hash": projection.projection_hash,
        "site_plan_hash": plan.site_plan_hash,
        "planner_operation_key": planner_operation_key,
        "generation_context_contract_version": "v1",
        "constraint_projection_contract_version": projection.contract_version,
        "site_plan_contract_version": plan.contract_version,
        "planner_contract_version": "v1",
        "policy_version": "v1",
        "validator_version": plan.validator_version,
        "selected_design": selected_design.model_dump(mode="json"),
        "site_plan": _site_plan_identity(plan),
        "authorized_assets": [item.model_dump(mode="json") for item in assets],
        "authorized_content": [item.model_dump(mode="json") for item in content],
    }


def build_generator_v2_input_snapshot(
    *, context: GenerationContextV1, projection: PlannerConstraintProjectionV1,
    site_plan: SitePlanV1, planner_operation_key: str,
    selected_design: GeneratorV2SelectedDesignIdentityV1,
    authorized_assets: tuple[AuthorizedAssetReferenceV1, ...] = (),
    authorized_content: tuple[AuthorizedContentReferenceV1, ...] = (),
) -> GeneratorV2SnapshotResultV1:
    """Build a READY snapshot only from typed, already-authorized objects."""
    expected_projection = build_planner_constraint_projection_v1(context)
    if (
        projection.generation_context_hash != context.fingerprints.context_hash
        or projection.projection_hash != expected_projection.projection_hash
    ):
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("projection_hash_mismatch",))
    validation = validate_site_plan_v1(context, site_plan)
    if validation.status != "valid" or validation.plan is None:
        return GeneratorV2SnapshotResultV1(status="NOT_READY", reason_codes=("site_plan_invalid",))
    if not isinstance(site_plan, SitePlanV1) or site_plan.site_plan_hash != _site_plan_hash(site_plan, context.fingerprints.context_hash):
        return GeneratorV2SnapshotResultV1(status="NOT_READY", reason_codes=("site_plan_hash_mismatch",))
    plan = validation.plan
    if not plan.site_plan_hash:
        return GeneratorV2SnapshotResultV1(status="NOT_READY", reason_codes=("site_plan_hash_mismatch",))
    if any(item.kind == "authorized_logo" and not item.content_hash for item in authorized_assets):
        return GeneratorV2SnapshotResultV1(status="NOT_READY", reason_codes=("unauthorized_asset_reference",))
    try:
        material = _build_material(
            order_id=context.order.order_id, journey_project_id=context.order.journey_project_id,
            context_hash=context.fingerprints.context_hash, projection=projection, plan=plan,
            planner_operation_key=planner_operation_key, selected_design=selected_design,
            assets=authorized_assets, content=authorized_content,
        )
        payload = dict(material)
        payload["site_plan"] = plan.model_dump(mode="json")
        payload["generator_input_hash"] = _sha(material)
        snapshot = GeneratorV2InputSnapshotV1.model_validate(payload)
    except ValueError:
        return GeneratorV2SnapshotResultV1(status="NOT_READY", reason_codes=("site_plan_hash_mismatch",))
    return GeneratorV2SnapshotResultV1(status="READY", snapshot=snapshot)


def validate_generator_v2_snapshot_identity(
    snapshot: GeneratorV2InputSnapshotV1, *, generation_context_hash: str,
    constraint_projection_hash: str, site_plan_hash: str,
    planner_operation_key: str, selected_design: GeneratorV2SelectedDesignIdentityV1,
) -> GeneratorV2SnapshotResultV1:
    """Pure stale/mismatch check; it never rebuilds or replans."""
    if snapshot.generation_context_hash != generation_context_hash:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("context_hash_mismatch",))
    if snapshot.constraint_projection_hash != constraint_projection_hash:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("projection_hash_mismatch",))
    if snapshot.site_plan_hash != site_plan_hash:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("site_plan_hash_mismatch",))
    if snapshot.planner_operation_key != planner_operation_key:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("planner_operation_mismatch",))
    if snapshot.selected_design.design_direction != selected_design.design_direction or snapshot.selected_design.visual_reference_hash != selected_design.visual_reference_hash:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("design_identity_mismatch",))
    if snapshot.selected_design.interaction_preference != selected_design.interaction_preference:
        return GeneratorV2SnapshotResultV1(status="STALE_OR_MISMATCH", reason_codes=("interaction_identity_mismatch",))
    return GeneratorV2SnapshotResultV1(status="READY", snapshot=snapshot)


def generator_v2_implementation_operation_key(snapshot: GeneratorV2InputSnapshotV1) -> str:
    return _sha({
        "generator_input_hash": snapshot.generator_input_hash,
        "generator_input_contract_version": GENERATOR_INPUT_CONTRACT_VERSION,
        "implementation_strategy_version": IMPLEMENTATION_STRATEGY_VERSION,
    })
