from textwrap import dedent


class GenerationService:
    @staticmethod
    def build_brief_context(order) -> str:
        answers = order.brief_answers or {}
        lines = [f'- {k}: {v}' for k, v in answers.items()]
        if getattr(order, 'intake_mode', None):
            lines.append(f'- intake_mode: {order.intake_mode}')
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
    def generate_two_concepts(order, reused_context: dict | None = None) -> tuple[dict, dict]:
        brief = GenerationService.build_brief_context(order)
        reused_note = f'\nReused context: {reused_context}' if reused_context else ''

        concept_a = {
            'art_direction': 'bold-premium-conversion',
            'summary': 'Контрастная, более дерзкая и коммерчески активная концепция с учетом описания клиента или его референсов.',
            'html': dedent(f'''
                <section class="hero hero-a">
                  <h1>{order.business_name or 'Future website concept A'}</h1>
                  <p>Сильный продающий визуальный стиль с учетом входного сценария клиента.</p>
                  <pre>{brief}{reused_note}</pre>
                </section>
            ''').strip(),
        }
        concept_b = {
            'art_direction': 'clean-editorial-luxury',
            'summary': 'Более спокойная, дорогая и визуально премиальная концепция с учетом описания клиента или его референсов.',
            'html': dedent(f'''
                <section class="hero hero-b">
                  <h1>{order.business_name or 'Future website concept B'}</h1>
                  <p>Чистый editorial-подход с дорогой подачей и учетом входного сценария клиента.</p>
                  <pre>{brief}{reused_note}</pre>
                </section>
            ''').strip(),
        }
        return concept_a, concept_b

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
