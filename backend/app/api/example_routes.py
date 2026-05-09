from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.example_service import (
    ExampleServiceError,
    build_generation_profile,
    get_example_by_id,
    get_examples,
    validate_example_payload,
)


router = APIRouter(
    prefix="/api/examples",
    tags=["examples"],
)


class ExampleTrackPayload(BaseModel):
    order_id: str | None = None
    entry_source: str | None = None
    viewed_examples: list[str] = Field(default_factory=list)
    time_spent: dict[str, int | float] = Field(default_factory=dict)
    package: str | None = None
    business_type: str | None = None
    industry_group: str | None = None
    design_direction: str | None = None
    interaction_style: str | None = None


class ExampleSelectPayload(ExampleTrackPayload):
    selected_example_id: str


@router.get("")
def list_examples(
    package: str | None = None,
    industry_group: str | None = None,
    design_direction: str | None = None,
    interaction_style: str | None = None,
):
    try:
        return {
            "status": "ok",
            "examples": get_examples(
                package=package,
                industry_group=industry_group,
                design_direction=design_direction,
                interaction_style=interaction_style,
            ),
        }

    except ExampleServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{example_id}")
def example_detail(example_id: str):
    example = get_example_by_id(example_id)

    if not example:
        raise HTTPException(status_code=404, detail="Example not found.")

    return {
        "status": "ok",
        "example": example,
    }


@router.post("/track")
def track_examples(payload: ExampleTrackPayload):
    data: dict[str, Any] = payload.model_dump()

    return {
        "status": "ok",
        "message": "Example tracking payload received.",
        "tracking": data,
        "note": "This endpoint currently validates and returns tracking data. Database persistence can be added in the next stage.",
    }


@router.post("/select")
def select_example(payload: ExampleSelectPayload):
    data: dict[str, Any] = payload.model_dump()

    try:
        validated = validate_example_payload(data)
        generation_profile = build_generation_profile(data)

    except ExampleServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "ok",
        "message": "Selected example accepted.",
        "selected_example": validated.get("selected_example"),
        "package_mismatch_warning": validated.get("package_mismatch_warning"),
        "interaction_style_compatible": validated.get("interaction_style_compatible"),
        "generation_profile": generation_profile,
    }