from app.models.order import PricingTier


class PricingService:
    COMPLEX_REFERENCE_KEYWORDS = {
        'popup', 'pop-up', 'modal', 'animation', 'animated', 'effect', 'effects', 'transition', 'overlay',
        'premium', 'luxury', 'expensive', 'interactive', 'microinteraction', 'hero animation', 'scroll effect',
        'floating', 'parallax', 'video', 'hover', 'exclusive', 'high-end', 'advanced'
    }

    @staticmethod
    def _style_text(payload) -> str:
        parts = []
        if getattr(payload, 'desired_site_description', None):
            parts.append(str(payload.desired_site_description))
        if getattr(payload, 'reference_site_notes', None):
            parts.append(str(payload.reference_site_notes))
        for item in getattr(payload, 'reference_sites', []) or []:
            if getattr(item, 'notes', None):
                parts.append(str(item.notes))
        answers = getattr(payload, 'answers', {}) or {}
        for key in ['reference_style_notes', 'desired_style_notes', 'desired_effects', 'visual_direction']:
            if answers.get(key):
                parts.append(str(answers[key]))
        return ' '.join(parts).lower()

    @classmethod
    def _has_expensive_reference_signals(cls, payload) -> bool:
        text = cls._style_text(payload)
        return any(keyword in text for keyword in cls.COMPLEX_REFERENCE_KEYWORDS)

    @classmethod
    def classify(cls, payload) -> tuple[str, int, str]:
        expensive_reference_signals = cls._has_expensive_reference_signals(payload)

        premium = any([
            payload.ecommerce,
            payload.cart,
            payload.catalog,
            payload.booking,
            payload.advanced_integrations,
        ])
        if premium:
            return (
                PricingTier.PREMIUM,
                1500,
                'This project includes advanced functionality, so it fits a higher-complexity build. If that scope feels too large, SiteFormo can offer a simplified option with fewer advanced features.',
            )

        business = any([
            payload.pages_requested > 3,
            payload.services_count > 3,
            payload.has_service_pages,
            payload.wants_leads,
            bool(payload.answers.get('needs_conversion_sections', False)),
            expensive_reference_signals,
        ])
        if business:
            if expensive_reference_signals:
                return (
                    PricingTier.BUSINESS,
                    900,
                    'The requested style suggests a stronger visual direction with effects or a more premium presentation. A simplified version can be offered if the client prefers a lower-budget route.',
                )
            return (
                PricingTier.BUSINESS,
                900,
                'This looks more complete than a basic landing page because it needs conversion structure and lead generation. The scope can be simplified if the client wants a leaner option.',
            )

        return (
            PricingTier.STARTER,
            600,
            'This is a focused landing page without advanced functionality or premium effects, so the starter package is the best fit.',
        )
