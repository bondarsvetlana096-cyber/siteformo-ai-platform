from typing import Dict, Any, List


class DiviStyleGenerationService:
    """
    5 DISTINCT layout systems (real UX differences, not just colors)
    """

    def generate_layout_spec(self, brief: Dict[str, Any], variant: int = 0) -> Dict[str, Any]:
        layouts = self._get_layouts(brief)
        return layouts[variant % len(layouts)]

    def _get_layouts(self, brief: Dict[str, Any]) -> List[Dict[str, Any]]:
        business = brief.get("business_type", "business")

        return [

            # 🔵 1. MODERN SAAS (clean, product style)
            {
                "style": "modern_saas",
                "design_system": self._design("#0A7CFF"),
                "sections": [
                    {"type": "navbar"},
                    {"type": "hero_center"},
                    {"type": "logos"},
                    {"type": "features_grid"},
                    {"type": "cta_inline"},
                ],
            },

            # 🟣 2. PREMIUM AGENCY (dark, high-end)
            {
                "style": "premium_agency",
                "design_system": self._design("#111111"),
                "sections": [
                    {"type": "navbar_dark"},
                    {"type": "hero_split"},
                    {"type": "portfolio_grid"},
                    {"type": "testimonials_large"},
                    {"type": "cta_big"},
                ],
            },

            # 🟠 3. BOLD MARKETING (conversion focused)
            {
                "style": "bold_conversion",
                "design_system": self._design("#FF6A00"),
                "sections": [
                    {"type": "navbar"},
                    {"type": "hero_big"},
                    {"type": "benefits_blocks"},
                    {"type": "stats_bar"},
                    {"type": "cta_strong"},
                ],
            },

            # 🟢 4. MINIMAL LUXURY (clean & elegant)
            {
                "style": "minimal_luxury",
                "design_system": self._design("#2ECC71"),
                "sections": [
                    {"type": "navbar_minimal"},
                    {"type": "hero_minimal"},
                    {"type": "services_cards"},
                    {"type": "about_split"},
                    {"type": "cta_clean"},
                ],
            },

            # 🔴 5. CREATIVE STUDIO (visual, bold)
            {
                "style": "creative_studio",
                "design_system": self._design("#E74C3C"),
                "sections": [
                    {"type": "navbar_creative"},
                    {"type": "hero_visual"},
                    {"type": "gallery_masonry"},
                    {"type": "process_steps"},
                    {"type": "cta_creative"},
                ],
            },
        ]

    def _design(self, primary: str) -> Dict[str, Any]:
        return {
            "colors": {
                "primary": primary,
                "secondary": "#111111",
                "background": "#FFFFFF",
            },
            "fonts": {
                "heading": "Inter",
                "body": "Inter"
            },
            "buttons": {
                "style": "rounded"
            }
        }