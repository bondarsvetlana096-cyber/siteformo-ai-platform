from app.services.i18n_service import I18nService
from types import SimpleNamespace

from app.services.pricing_service import PricingService


starter = SimpleNamespace(
    ecommerce=False,
    cart=False,
    catalog=False,
    booking=False,
    advanced_integrations=False,
    pages_requested=2,
    services_count=2,
    has_service_pages=False,
    wants_leads=False,
    answers={},
)

business = SimpleNamespace(
    ecommerce=False,
    cart=False,
    catalog=False,
    booking=False,
    advanced_integrations=False,
    pages_requested=5,
    services_count=4,
    has_service_pages=True,
    wants_leads=True,
    answers={'needs_conversion_sections': True},
)

premium = SimpleNamespace(
    ecommerce=True,
    cart=True,
    catalog=False,
    booking=False,
    advanced_integrations=False,
    pages_requested=3,
    services_count=2,
    has_service_pages=False,
    wants_leads=True,
    answers={},
)

assert PricingService.classify(starter)[1] == 600
assert PricingService.classify(business)[1] == 900
assert PricingService.classify(premium)[1] == 1500
print('pricing preview checks passed')

assert I18nService.normalize_language('DE') == 'de'
assert I18nService.normalize_language('pt') == 'en'
