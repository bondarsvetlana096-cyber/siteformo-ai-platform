from __future__ import annotations

from app.services.contact_delivery.example_scope import (
    ExampleScopeError,
    TrustedExampleScope,
    resolve_trusted_example,
)


def trusted_example_for_origin(
    origin: str | None, requested_example_id: str | None = None
) -> TrustedExampleScope | None:
    """Compatibility wrapper over the single canonical Example/Origin resolver."""
    try:
        return resolve_trusted_example(origin, requested_example_id)
    except ExampleScopeError:
        return None
