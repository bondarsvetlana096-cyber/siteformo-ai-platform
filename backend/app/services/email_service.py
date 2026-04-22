from app.core.config import settings
from app.services.approval_service import ApprovalService


class OwnerEmailComposer:
    @staticmethod
    def compose_order_email(order) -> dict:
        approve_url = ApprovalService.build_action_url(order.id, 'approve')
        reject_url = ApprovalService.build_action_url(order.id, 'reject')
        return {
            'to': settings.owner_email,
            'subject': f'Новый заказ: {order.business_name or order.id}',
            'html': f'''
                <h1>Новый заказ SiteFormo</h1>
                <p><b>Order ID:</b> {order.id}</p>
                <p><b>Business:</b> {order.business_name or '-'}</p>
                <p><b>Tier:</b> {order.recommended_tier}</p>
                <p><b>Estimated Price:</b> €{order.estimated_price_eur}</p>
                <p><b>Reasoning:</b> {order.pricing_reasoning or '-'}</p>
                <p><a href="{approve_url}">Approve</a></p>
                <p><a href="{reject_url}">Reject</a></p>
            ''',
        }

    @staticmethod
    def compose_delivery_email(order, brief_markdown: str) -> dict:
        return {
            'to': settings.owner_email,
            'subject': f'Финальный пакет готов: {order.business_name or order.id}',
            'html': f'<h1>Финальный пакет готов</h1><p>Order ID: {order.id}</p><pre>{brief_markdown}</pre>',
        }
