from pydantic import BaseModel, Field


class ReferenceSiteInput(BaseModel):
    url: str
    notes: str | None = None


class IntakePayload(BaseModel):
    channel: str = Field(description='web / whatsapp / telegram')
    intake_mode: str = Field(default='describe', description='reference_sites or describe')
    preferred_language: str = Field(default='en', description='en / de / fr / it / es')
    email: str | None = None
    phone: str | None = None
    telegram_handle: str | None = None
    fingerprint: str | None = None
    ip_hash: str | None = None
    business_name: str | None = None
    site_type: str | None = None
    source_url: str | None = None
    desired_site_description: str | None = None
    reference_site_url: str | None = None
    reference_site_notes: str | None = None
    reference_sites: list[ReferenceSiteInput] = Field(default_factory=list)
    answers: dict = Field(default_factory=dict)
    pages_requested: int = 1
    services_count: int = 1
    has_service_pages: bool = False
    wants_leads: bool = True
    ecommerce: bool = False
    cart: bool = False
    catalog: bool = False
    booking: bool = False
    advanced_integrations: bool = False


class IntakeResponse(BaseModel):
    client_id: str
    order_id: str
    reused_context: bool
    reused_order_id: str | None = None
    recommended_tier: str
    estimated_price_eur: int
    pricing_reasoning: str
    preferred_language: str
    status: str


class ApprovalResponse(BaseModel):
    order_id: str
    status: str
    message: str
