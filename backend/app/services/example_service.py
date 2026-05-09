import json
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "siteformo_examples.json"


class ExampleServiceError(Exception):
    pass


def _normalise(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower()


def load_examples() -> list[dict[str, Any]]:
    if not DATA_PATH.exists():
        raise ExampleServiceError(f"Example catalog not found: {DATA_PATH}")

    with DATA_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ExampleServiceError("Example catalog must be a JSON list.")

    return data


def get_examples(
    package: str | None = None,
    industry_group: str | None = None,
    design_direction: str | None = None,
    interaction_style: str | None = None,
) -> list[dict[str, Any]]:
    examples = load_examples()

    package = _normalise(package)
    industry_group = _normalise(industry_group)
    design_direction = _normalise(design_direction)
    interaction_style = _normalise(interaction_style)

    result: list[dict[str, Any]] = []

    for example in examples:
        if package and _normalise(example.get("package")) != package:
            continue

        if industry_group and _normalise(example.get("industry_group")) != industry_group:
            continue

        if design_direction and _normalise(example.get("design_direction")) != design_direction:
            continue

        if interaction_style:
            recommended = [
                _normalise(item)
                for item in example.get("recommended_interaction_styles", [])
            ]

            # Starter has no interaction style stage.
            if example.get("package") != "starter" and interaction_style not in recommended:
                continue

        result.append(example)

    return result


def get_example_by_id(example_id: str) -> dict[str, Any] | None:
    example_id = _normalise(example_id)

    for example in load_examples():
        if _normalise(example.get("id")) == example_id:
            return example

    return None


def validate_example_payload(payload: dict[str, Any]) -> dict[str, Any]:
    selected_example_id = payload.get("selected_example_id")
    selected_example = None

    if selected_example_id:
        selected_example = get_example_by_id(selected_example_id)

        if selected_example is None:
            raise ExampleServiceError(f"Unknown selected_example_id: {selected_example_id}")

    package = _normalise(payload.get("package"))
    interaction_style = _normalise(payload.get("interaction_style"))

    if selected_example:
        example_package = _normalise(selected_example.get("package"))

        if package and example_package != package:
            payload["package_mismatch_warning"] = {
                "selected_package": package,
                "example_package": example_package,
                "message": "Selected example belongs to a different package. The project may need package adjustment.",
            }

        if example_package != "starter" and interaction_style:
            recommended = [
                _normalise(item)
                for item in selected_example.get("recommended_interaction_styles", [])
            ]

            payload["interaction_style_compatible"] = interaction_style in recommended

        else:
            payload["interaction_style_compatible"] = True

        payload["selected_example"] = selected_example

    payload["example_signal_ready"] = bool(selected_example)

    return payload


def build_generation_profile(payload: dict[str, Any]) -> dict[str, Any]:
    validated = validate_example_payload(payload)

    selected_example = validated.get("selected_example") or {}

    return {
        "order_id": validated.get("order_id"),
        "entry_source": validated.get("entry_source"),
        "package": validated.get("package") or selected_example.get("package"),
        "business_type": validated.get("business_type"),
        "industry_group": validated.get("industry_group") or selected_example.get("industry_group"),
        "selected_example_id": validated.get("selected_example_id"),
        "selected_example_title": selected_example.get("title"),
        "selected_example_description": selected_example.get("description"),
        "design_direction": validated.get("design_direction") or selected_example.get("design_direction"),
        "interaction_style": validated.get("interaction_style"),
        "viewed_examples": validated.get("viewed_examples", []),
        "time_spent": validated.get("time_spent", {}),
        "complexity": selected_example.get("complexity"),
        "generation_logic": "selected_example + design_direction + interaction_style + package + business_type",
        "preview_access_is_not_delivery": True,
        "zip_delivery_requires_final_approval": True,
    }