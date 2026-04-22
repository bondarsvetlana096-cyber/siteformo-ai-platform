from app.models.order import PricingTier


class PricingService:
    COMPLEX_REFERENCE_KEYWORDS = {
        'popup', 'pop-up', 'modal', 'animation', 'animated', 'effect', 'effects', 'transition', 'overlay',
        'premium', 'luxury', 'expensive', 'interactive', 'microinteraction', 'hero animation', 'scroll effect',
        'floating', 'parallax', 'video', 'hover', 'дорог', 'эффект', 'анимац', 'переход', 'всплыва', 'люкс'
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
                'Похоже, проект включает более сложную функциональность. Такой уровень обычно относится к более дорогой разработке. Если этот формат вам подходит, мы продолжим обработку заказа. Если нет, можно упростить проект и перейти к более бюджетному варианту без части функций.',
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
                    'Похоже, вам нравится более дорогой уровень сайта — с сильной подачей, эффектами или более сложной визуальной структурой. Если вас устраивает такой уровень, мы продолжим обработку заказа в этом направлении. Если нет, я могу предложить более бюджетный вариант в похожем стиле.',
                )
            return (
                PricingTier.BUSINESS,
                900,
                'Похоже, вам нужен уже не просто базовый лендинг. С учетом структуры сайта и задачи на привлечение клиентов лучше подходит расширенный вариант. Если вас устраивает этот уровень, мы продолжим обработку заказа. Если нет, можно упростить проект и подобрать более бюджетную альтернативу.',
            )

        return (
            PricingTier.STARTER,
            600,
            'Это базовый вариант без сложной логики и без дорогих эффектов, поэтому здесь подходит формат простого лендинга за 600 евро.',
        )
