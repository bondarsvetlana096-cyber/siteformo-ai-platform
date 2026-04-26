from app.models.order import PricingTier


class PricingService:
    BUSINESS_REFERENCE_KEYWORDS = {
        'custom design', 'conversion', 'sales funnel', 'lead funnel',
        'landing funnel', 'professional', 'modern', 'premium look',
        'sections', 'testimonials', 'pricing', 'portfolio'
    }

    PREMIUM_REFERENCE_KEYWORDS = {
        'ecommerce', 'e-commerce', 'shop', 'store', 'cart', 'checkout',
        'catalog', 'booking system', 'appointment', 'calendar',
        'crm', 'automation', 'ai', 'chatbot', 'whatsapp automation',
        'telegram automation', 'payment', 'stripe', 'paypal',
        'membership', 'login', 'dashboard', 'portal',
        'multilingual', 'multi-language', 'advanced integration',
        'api integration', 'custom animation', 'parallax',
        'video background', 'interactive', 'complex', 'saas',
        'marketplace', 'subscription'
    }

    @staticmethod
    def _style_text(payload) -> str:
        parts = []

        for field in [
            'desired_site_description',
            'reference_site_notes',
            'source_url',
        ]:
            value = getattr(payload, field, None)
            if value:
                parts.append(str(value))

        for item in getattr(payload, 'reference_sites', []) or []:
            if getattr(item, 'notes', None):
                parts.append(str(item.notes))
            if getattr(item, 'url', None):
                parts.append(str(item.url))

        answers = getattr(payload, 'answers', {}) or {}
        for key, value in answers.items():
            if value:
                parts.append(str(value))

        return ' '.join(parts).lower()

    @classmethod
    def _has_business_signals(cls, payload) -> bool:
        text = cls._style_text(payload)
        return any(keyword in text for keyword in cls.BUSINESS_REFERENCE_KEYWORDS)

    @classmethod
    def _has_premium_signals(cls, payload) -> bool:
        text = cls._style_text(payload)
        return any(keyword in text for keyword in cls.PREMIUM_REFERENCE_KEYWORDS)

    @classmethod
    def classify(cls, payload) -> tuple[str, int, str]:
        premium_signals = cls._has_premium_signals(payload)
        business_signals = cls._has_business_signals(payload)

        premium = any([
            getattr(payload, 'ecommerce', False),
            getattr(payload, 'cart', False),
            getattr(payload, 'catalog', False),
            getattr(payload, 'booking', False),
            getattr(payload, 'advanced_integrations', False),
            premium_signals,
        ])

        if premium:
            return (
                PricingTier.PREMIUM,
                1500,
                'This project includes advanced functionality, integrations, automation, booking, ecommerce, or a more complex build. That fits the Premium Website package. If the scope feels too large, SiteFormo can offer a simplified Business or Starter version.',
            )

        business = any([
            getattr(payload, 'pages_requested', 1) > 3,
            getattr(payload, 'services_count', 1) > 3,
            getattr(payload, 'has_service_pages', False),
            bool((getattr(payload, 'answers', {}) or {}).get('needs_conversion_sections', False)),
            business_signals,
        ])

        if business:
            return (
                PricingTier.BUSINESS,
                900,
                'This project needs a stronger landing page structure, custom presentation, or conversion-focused sections. The Business Website package is the best fit.',
            )

        return (
            PricingTier.STARTER,
            600,
            'This is a focused landing page without advanced functionality, ecommerce, booking, automation, or complex integrations, so the Starter Website package is the best fit.',
        )