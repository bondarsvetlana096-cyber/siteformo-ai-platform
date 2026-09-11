from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    owner_bypass: bool = False
    payment_required: bool = True


class ApprovalResponse(BaseModel):
    order_id: str
    status: str
    message: str


class PreferredContactV2(BaseModel):
    channel: Literal["whatsapp", "telegram", "email"]
    value: str = Field(min_length=1, max_length=320)
    normalized_value: str = Field(min_length=1, max_length=320)
    purpose: Literal["operational_communication"] = "operational_communication"
    display_on_generated_website: Literal[False] = False


class ExistingWebsiteAnalysisV2(BaseModel):
    source: Literal["existing_website"] = "existing_website"
    status: Literal["unconfirmed"] = "unconfirmed"
    data: dict = Field(default_factory=dict)


class ExistingWebsiteV2(BaseModel):
    has_existing_website: bool
    url: str | None = None
    analysis: ExistingWebsiteAnalysisV2 | None = None

    @model_validator(mode="after")
    def validate_url_choice(self):
        self.url = self.url.strip() if self.url else None
        if self.has_existing_website and not self.url:
            raise ValueError("URL is required when an existing website is selected")
        if not self.has_existing_website:
            self.url = None
            self.analysis = None
        return self


class PackageQualificationV2(BaseModel):
    status: Literal["unqualified", "candidate_unconfirmed"] = "unqualified"
    candidate_package: str | None = None
    source: Literal["existing_website_analysis"] | None = None

    @model_validator(mode="after")
    def validate_candidate(self):
        if self.status == "unqualified":
            self.candidate_package = None
            self.source = None
        elif not self.candidate_package or self.source != "existing_website_analysis":
            raise ValueError("An unconfirmed candidate needs a package and existing-site source")
        return self


class AssistantContextV2(BaseModel):
    current_step_id: Literal["q1_intro", "q1_project_type", "q1_contact", "q1_existing_website", "q1_complete"]
    enabled: Literal[False] = False


class Q1V2Payload(BaseModel):
    flow_version: Literal["q1_v2"] = "q1_v2"
    schema_version: Literal[2] = 2
    project_class_intent: Literal["one_page", "business_site", "ecommerce_catalog", "online_service_platform", "unsure"]
    preferred_contact: PreferredContactV2
    existing_website: ExistingWebsiteV2
    examples_context: dict = Field(default_factory=dict)
    package_browsing_context: dict | None = None
    package_qualification: PackageQualificationV2 = Field(default_factory=PackageQualificationV2)
    assistant_context: AssistantContextV2 = Field(default_factory=lambda: AssistantContextV2(current_step_id="q1_complete"))


class Q1ProjectResponse(BaseModel):
    order_id: str
    journey_id: str
    created: bool


class Q1ProjectRequest(BaseModel):
    order_id: str | None = None
    handoff_id: str | None = None
    start_new_project: bool = False


class Q1V2Response(BaseModel):
    order_id: str
    journey_id: str
    flow_version: str
    schema_version: int
    idempotent: bool
