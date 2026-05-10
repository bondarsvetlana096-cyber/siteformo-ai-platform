from __future__ import annotations

# Backwards-compatible wrapper.
# New code should import app.services.package_rules_service directly.
from app.services.package_rules_service import PACKAGE_RULES as PACKAGE_QUALITY_RULES
from app.services.package_rules_service import get_package_rules, normalize_package
