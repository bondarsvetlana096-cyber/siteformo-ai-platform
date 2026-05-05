from typing import Dict, Any


class DiviStyleGenerationService:
    """
    Generates structured, premium website layouts similar to Divi designs.
    NOT raw HTML first — but structured layout system.
    """

    def generate_layout_spec(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 1: Create design system + sections
        """

        business_type = brief.get("business_type", "business")
        style = brief.get("style", "modern")
        pages = brief.get("pages", ["home"])

        return {
            "design_system": {
                "colors": {
                    "primary": "#0A7CFF",
                    "secondary": "#111111",
                    "background": "#FFFFFF",
                    "accent": "#F5F7FA"
                },
                "fonts": {
                    "heading": "Inter",
                    "body": "Inter"
                },
                "buttons": {
                    "style": "rounded",
                    "size": "medium"
                },
                "spacing": "balanced"
            },

            "sections": [
                {
                    "type": "hero",
                    "headline": f"{business_type.capitalize()} that stands out",
                    "subheadline": "We help you grow faster with a modern website",
                    "cta": "Get started"
                },
                {
                    "type": "trust",
                    "items": ["Trusted by clients", "5-star reviews", "Fast delivery"]
                },
                {
                    "type": "services",
                    "style": "cards",
                    "items": [
                        "Service 1",
                        "Service 2",
                        "Service 3"
                    ]
                },
                {
                    "type": "portfolio",
                    "style": "grid",
                    "items": 6
                },
                {
                    "type": "process",
                    "steps": [
                        "Contact",
                        "Planning",
                        "Design",
                        "Launch"
                    ]
                },
                {
                    "type": "testimonials",
                    "style": "slider"
                },
                {
                    "type": "cta",
                    "headline": "Ready to start?",
                    "button": "Contact us"
                },
                {
                    "type": "footer"
                }
            ],

            "pages": pages,
            "style": style
        }

    def generate_html(self, spec: Dict[str, Any]) -> str:
        """
        Step 2: Convert spec into clean HTML (Divi-like structure)
        """

        html = "<html><head><title>SiteFormo</title></head><body>"

        for section in spec["sections"]:
            section_type = section["type"]

            if section_type == "hero":
                html += f"""
                <section style="padding:80px;text-align:center;">
                    <h1>{section['headline']}</h1>
                    <p>{section['subheadline']}</p>
                    <button>{section['cta']}</button>
                </section>
                """

            elif section_type == "services":
                html += "<section><div style='display:flex;gap:20px;'>"
                for item in section["items"]:
                    html += f"<div><h3>{item}</h3></div>"
                html += "</div></section>"

            elif section_type == "cta":
                html += f"""
                <section style="text-align:center;padding:60px;">
                    <h2>{section['headline']}</h2>
                    <button>{section['button']}</button>
                </section>
                """

            elif section_type == "footer":
                html += "<footer style='padding:40px;text-align:center;'>© SiteFormo</footer>"

        html += "</body></html>"
        return html