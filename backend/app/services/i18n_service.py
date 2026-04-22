from app.models.order import SupportedLanguage


class I18nService:
    DEFAULT_LANGUAGE = SupportedLanguage.EN

    @staticmethod
    def normalize_language(language: str | None) -> str:
        if not language:
            return I18nService.DEFAULT_LANGUAGE
        value = language.strip().lower()
        return value if value in SupportedLanguage.ALL else I18nService.DEFAULT_LANGUAGE
