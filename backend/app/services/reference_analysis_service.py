class ReferenceAnalysisService:
    @staticmethod
    def summarize(reference_sites: list[dict] | None, fallback_url: str | None = None, fallback_notes: str | None = None, desired_site_description: str | None = None) -> str | None:
        refs = list(reference_sites or [])
        if fallback_url:
            refs.append({'url': fallback_url, 'notes': fallback_notes})

        notes_parts = [str(item.get('notes') or '') for item in refs]
        if desired_site_description:
            notes_parts.append(desired_site_description)
        notes = ' '.join(notes_parts).lower()

        if not refs and not desired_site_description:
            return None

        signals = []
        if any(token in notes for token in ['premium', 'luxury', 'expensive', 'дорого', 'дорог']):
            signals.append('premium-look')
        if any(token in notes for token in ['effect', 'effects', 'animation', 'transition', 'popup', 'overlay', 'hover', 'video', 'parallax', 'эффект', 'анимац', 'переход', 'всплыва']):
            signals.append('interactive-effects')
        if any(token in notes for token in ['hero', 'structure', 'layout', 'структура', 'scroll']):
            signals.append('layout-driven')
        if desired_site_description:
            signals.append('described-by-client')
        if refs:
            signals.append('reference-sites-provided')
        if not signals:
            signals.append('style-direction-provided')
        return ', '.join(dict.fromkeys(signals))
