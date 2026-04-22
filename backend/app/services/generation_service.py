from __future__ import annotations

import json
from textwrap import dedent

from app.services.openai_service import OpenAIService


class GenerationService:
    @staticmethod
    def build_brief_context(order) -> str:
        answers = order.brief_answers or {}
        lines = [f'- {k}: {v}' for k, v in answers.items()]
        if getattr(order, 'intake_mode', None):
            lines.append(f'- intake_mode: {order.intake_mode}')
        if getattr(order, 'business_name', None):
            lines.append(f'- business_name: {order.business_name}')
        if getattr(order, 'site_type', None):
            lines.append(f'- site_type: {order.site_type}')
        if getattr(order, 'desired_site_description', None):
            lines.append(f'- desired_site_description: {order.desired_site_description}')
        if getattr(order, 'reference_sites', None):
            lines.append(f'- reference_sites: {order.reference_sites}')
        if getattr(order, 'reference_analysis_summary', None):
            lines.append(f'- reference_analysis_summary: {order.reference_analysis_summary}')
        if getattr(order, 'reference_site_url', None):
            lines.append(f'- reference_site_url: {order.reference_site_url}')
        if getattr(order, 'reference_site_notes', None):
            lines.append(f'- reference_site_notes: {order.reference_site_notes}')
        return '\n'.join(lines)

    @staticmethod
    def _fallback_concepts(order, brief: str, reused_context: dict | None = None) -> tuple[dict, dict]:
        reused_note = f'\nReused context: {json.dumps(reused_context, ensure_ascii=False)}' if reused_context else ''
        concept_a = {
            'art_direction': 'bold-premium-conversion',
            'summary': 'Bold premium direction focused on strong conversion, trust, and clear calls to action.',
            'html': dedent(
                f'''
                <section class="hero hero-a">
                  <h1>{order.business_name or 'Future website concept A'}</h1>
                  <p>Conversion-focused design with strong positioning and a clear CTA.</p>
                  <pre>{brief}{reused_note}</pre>
                </section>
                '''
            ).strip(),
        }
        concept_b = {
            'art_direction': 'clean-editorial-luxury',
            'summary': 'Calm editorial direction with a premium visual tone and a more refined presentation.',
            'html': dedent(
                f'''
                <section class="hero hero-b">
                  <h1>{order.business_name or 'Future website concept B'}</h1>
                  <p>Editorial premium design with elegant structure and softer persuasion.</p>
                  <pre>{brief}{reused_note}</pre>
                </section>
                '''
            ).strip(),
        }
        return concept_a, concept_b

    @staticmethod
    def _try_ai_concepts(order, brief: str, reused_context: dict | None = None) -> tuple[dict, dict] | None:
        if not OpenAIService.is_configured():
            return None

        prompt = dedent(
            f'''
            Create two different homepage concepts for a website sales intake.

            Return valid JSON only in this exact structure:
            {{
              "concept_a": {{"art_direction": "...", "summary": "...", "headline": "...", "subheadline": "...", "cta": "..."}},
              "concept_b": {{"art_direction": "...", "summary": "...", "headline": "...", "subheadline": "...", "cta": "..."}}
            }}

            Rules:
            - Output in English.
            - Keep each summary under 30 words.
            - Make the concepts meaningfully different.
            - Reflect the client brief, site type, and references.
            - Do not use markdown fences.

            Brief:
            {brief}

            Reused context:
            {json.dumps(reused_context, ensure_ascii=False) if reused_context else 'none'}
            '''
        ).strip()

        raw = OpenAIService.refine_reply(
            system_prompt='You are a website concept generator that returns strict JSON only.',
            user_text=prompt,
            fallback_text='',
        )
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        def to_concept(item: dict, css_class: str) -> dict:
            headline = item.get('headline') or (order.business_name or 'Website concept')
            subheadline = item.get('subheadline') or 'Homepage concept draft'
            cta = item.get('cta') or 'Get started'
            return {
                'art_direction': item.get('art_direction') or css_class,
                'summary': item.get('summary') or subheadline,
                'html': dedent(
                    f'''
                    <section class="hero {css_class}">
                      <h1>{headline}</h1>
                      <p>{subheadline}</p>
                      <a href="#contact">{cta}</a>
                    </section>
                    '''
                ).strip(),
            }

        a = data.get('concept_a')
        b = data.get('concept_b')
        if not isinstance(a, dict) or not isinstance(b, dict):
            return None
        return to_concept(a, 'hero-a'), to_concept(b, 'hero-b')

    @staticmethod
    def generate_two_concepts(order, reused_context: dict | None = None) -> tuple[dict, dict]:
        brief = GenerationService.build_brief_context(order)
        ai_concepts = GenerationService._try_ai_concepts(order, brief, reused_context=reused_context)
        if ai_concepts:
            return ai_concepts
        return GenerationService._fallback_concepts(order, brief, reused_context=reused_context)

    @staticmethod
    def build_final_divi_package(order, selected_html: str) -> tuple[str, str]:
        answers = order.brief_answers or {}
        lines = [f'- **{k}**: {v}' for k, v in answers.items()]
        if getattr(order, 'intake_mode', None):
            lines.append(f'- **intake_mode**: {order.intake_mode}')
        if getattr(order, 'desired_site_description', None):
            lines.append(f'- **desired_site_description**: {order.desired_site_description}')
        if getattr(order, 'reference_sites', None):
            lines.append(f'- **reference_sites**: {order.reference_sites}')
        if getattr(order, 'reference_analysis_summary', None):
            lines.append(f'- **reference_analysis_summary**: {order.reference_analysis_summary}')
        if getattr(order, 'reference_site_url', None):
            lines.append(f'- **reference_site_url**: {order.reference_site_url}')
        if getattr(order, 'reference_site_notes', None):
            lines.append(f'- **reference_site_notes**: {order.reference_site_notes}')
        brief_markdown = '\n'.join(lines)
        divi_html = f'<!-- Divi 5 compatible export -->\n{selected_html}'
        return divi_html, brief_markdown
