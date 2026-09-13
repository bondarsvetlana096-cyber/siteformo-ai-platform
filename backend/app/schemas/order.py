from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class BusinessIdentityQ2V2(BaseModel):
    status: Literal["business_name", "personal_name", "temporary_pending"]
    name: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_name(self):
        self.name = self.name.strip() if self.name else None
        if self.status != "temporary_pending" and not self.name:
            raise ValueError("A factual business or personal name is required")
        if self.status == "temporary_pending":
            self.name = None
        return self


class BusinessActivityQ2V2(BaseModel):
    niche: str = Field(min_length=2, max_length=120)
    broad_model: Literal[
        "local_service", "consultant_expert", "clinic_beauty_wellness",
        "construction_renovation", "restaurant_cafe", "portfolio_creative", "other",
    ]
    other_clarification: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_other(self):
        self.niche = self.niche.strip()
        self.other_clarification = self.other_clarification.strip() if self.other_clarification else None
        if self.broad_model == "other" and not self.other_clarification:
            raise ValueError("A short factual clarification is required for Other")
        if self.broad_model != "other":
            self.other_clarification = None
        return self


class LocationQ2V2(BaseModel):
    public_address: str | None = Field(default=None, max_length=320)
    service_area: str | None = Field(default=None, max_length=240)
    multiple_locations_summary: str | None = Field(default=None, max_length=320)


class FunctionRequirementQ2V2(BaseModel):
    key: Literal[
        "standard_content_contact", "contact_enquiry", "direct_contact_channel",
        "blog_news", "newsletter", "booking", "catalogue_display",
        "transactional_ecommerce", "customer_account_login", "portal_membership",
        "multilingual", "advanced_form", "search_filter_compare", "custom_other",
    ]
    confirmed: bool = True


class MediaQ2V2(BaseModel):
    photos: Literal["client_provides", "siteformo_selects", "none"]
    client_photos_timing: Literal["available", "later"] | None = None
    video: Literal["none", "client_has", "client_later"]

    @model_validator(mode="after")
    def validate_photo_timing(self):
        if self.photos == "client_provides" and self.client_photos_timing is None:
            raise ValueError("Client photo timing is required")
        if self.photos != "client_provides":
            self.client_photos_timing = None
        return self


class SocialPresenceQ2V2(BaseModel):
    expected_channels: list[Literal["instagram", "facebook", "tiktok", "linkedin", "youtube", "telegram", "other", "none"]] = Field(min_length=1)
    other_platform: str | None = Field(default=None, max_length=80)
    links: Literal["pending"] = "pending"
    presentation: Literal["flexible"] = "flexible"

    @model_validator(mode="after")
    def validate_channels(self):
        unique = list(dict.fromkeys(self.expected_channels))
        if "none" in unique and len(unique) > 1:
            raise ValueError("None cannot be combined with social channels")
        self.expected_channels = unique
        self.other_platform = self.other_platform.strip() if self.other_platform else None
        if "other" in unique and not self.other_platform:
            raise ValueError("A short platform name is required for Other")
        if "other" not in unique:
            self.other_platform = None
        return self


class LogoQ2V2(BaseModel):
    choice: Literal["client_has", "siteformo_simple_logo", "none"]
    price_eur: int = 0
    scope: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_logo_contract(self):
        if self.choice == "siteformo_simple_logo":
            if self.price_eur != 100:
                raise ValueError("Simple SiteFormo Logo is EUR 100")
            required = {
                "one_direction": True, "one_revision": True, "web_ready_delivery": True,
                "full_branding": False, "naming": False, "trademark_or_legal_clearance": False,
            }
            if self.scope != required:
                raise ValueError("Simple logo scope must match the limited product contract")
        else:
            self.price_eur = 0
            self.scope = {}
        return self


class DeliveryContextQ2V2(BaseModel):
    hosting_status: Literal["already_have", "do_not_have", "not_sure"]
    hosting_help_included: Literal[True] = True
    domain_and_hosting_client_owned: Literal[True] = True
    installation_selected: bool = False
    installation_price_eur: int = 0

    @model_validator(mode="after")
    def validate_installation(self):
        expected = 125 if self.installation_selected else 0
        if self.installation_price_eur != expected:
            raise ValueError("Installation is EUR 125 when explicitly selected")
        return self


class PaidStructuralOptionQ2V2(BaseModel):
    key: Literal["video_entry_page", "additional_page"]
    quantity: int = Field(default=1, ge=1)
    price_eur_each: Literal[120] = 120
    counts_as_page: Literal[True] = True
    explicit_confirmed: bool


class PackageContextQ2V2(BaseModel):
    starting_package: Literal["starter", "business", "reference", "advanced"] | None = None
    source: Literal["package_browsing_context", "direct_entry"] = "direct_entry"
    rule_version: Literal["ireland_accepted_v1"] = "ireland_accepted_v1"


class AnalysisHintsQ2V2(BaseModel):
    source: Literal["existing_website"] | None = None
    status: Literal["unconfirmed"] | None = None
    business_hints: list[str] = Field(default_factory=list)
    function_hints: list[str] = Field(default_factory=list)
    complexity_hints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self):
        if any((self.business_hints, self.function_hints, self.complexity_hints)):
            if self.source != "existing_website" or self.status != "unconfirmed":
                raise ValueError("Analysis hints must remain unconfirmed existing-site hints")
        return self


class AssistantContextQ2V2(BaseModel):
    current_step_id: Literal[
        "q2_intro", "q2_business_identity", "q2_business_activity", "q2_location",
        "q2_audience_goal", "q2_functions", "q2_assets", "q2_delivery",
        "q2_review", "q2_package_confirmation",
    ]
    enabled: Literal[False] = False


class Q2V2Payload(BaseModel):
    flow_version: Literal["q2_v2"] = "q2_v2"
    schema_version: Literal[2] = 2
    business_identity: BusinessIdentityQ2V2
    business_activity: BusinessActivityQ2V2
    operating_model: Literal["customer_location", "travel_to_customers", "both", "online_remote", "multiple_locations"]
    location: LocationQ2V2
    audience: Literal["individuals", "businesses", "both", "organisations_public_sector", "mixed_unsure"]
    primary_goal: Literal["enquiries", "calls", "quote_requests", "bookings", "online_sales", "physical_visits", "present_and_build_trust"]
    functions: list[FunctionRequirementQ2V2] = Field(default_factory=list)
    trust_materials: list[Literal["reviews", "project_photos", "certifications", "awards", "client_logos", "team_photos", "none_yet"]] = Field(min_length=1)
    media: MediaQ2V2
    social_presence: SocialPresenceQ2V2
    logo: LogoQ2V2
    delivery_context: DeliveryContextQ2V2
    paid_structural_options: list[PaidStructuralOptionQ2V2] = Field(default_factory=list)
    package_context: PackageContextQ2V2 = Field(default_factory=PackageContextQ2V2)
    analysis_hints: AnalysisHintsQ2V2 = Field(default_factory=AnalysisHintsQ2V2)
    assistant_context: AssistantContextQ2V2 = Field(default_factory=lambda: AssistantContextQ2V2(current_step_id="q2_intro"))
    legal_gate_confirmed: bool = False
    package_and_addons_confirmed: bool = False

    @model_validator(mode="after")
    def validate_q2_contract(self):
        if "none_yet" in self.trust_materials and len(self.trust_materials) > 1:
            raise ValueError("None yet cannot be combined with trust materials")
        if self.operating_model == "customer_location" and not self.location.public_address:
            raise ValueError("A public address is required when customers visit")
        if self.operating_model == "travel_to_customers" and not self.location.service_area:
            raise ValueError("A service area is required when travelling to customers")
        if self.operating_model == "both" and (not self.location.public_address or not self.location.service_area):
            raise ValueError("Address and service area are required for both operating modes")
        if self.operating_model == "multiple_locations" and not self.location.multiple_locations_summary:
            raise ValueError("A high-level location summary is required")
        keys = [item.key for item in self.functions]
        if len(keys) != len(set(keys)):
            raise ValueError("Function requirements must be unique")
        paid = [item for item in self.paid_structural_options if item.key == "video_entry_page"]
        if paid and (len(paid) != 1 or paid[0].quantity != 1):
            raise ValueError("Video Entry Page may be selected once")
        return self


class ScopeReasonQ2V2(BaseModel):
    reason_code: str
    evidence: dict = Field(default_factory=dict)
    package_effect: str | None = None


class UnresolvedRequirementQ2V2(BaseModel):
    reason_code: str
    requirement: str | int
    resolution: str


class ArchitecturePlannerInputQ2V2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_activity: BusinessActivityQ2V2
    operating_model: Literal["customer_location", "travel_to_customers", "both", "online_remote", "multiple_locations"]
    location: LocationQ2V2
    audience: Literal["individuals", "businesses", "both", "organisations_public_sector", "mixed_unsure"]
    primary_goal: Literal["enquiries", "calls", "quote_requests", "bookings", "online_sales", "physical_visits", "present_and_build_trust"]
    functions: list[FunctionRequirementQ2V2]
    trust_materials: list[str]
    media: MediaQ2V2
    social_presence: SocialPresenceQ2V2
    paid_structural_options: list[PaidStructuralOptionQ2V2]


class ProvisionalPageRoleQ2V2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: Literal[
        "primary_conversion", "services_or_offer", "contact_or_enquiry",
        "location", "projects_or_work", "business_trust", "content_hub",
        "catalogue", "booking", "customer_area", "video_entry",
    ]
    purpose: str = Field(min_length=3, max_length=240)
    requirements_served: list[str] = Field(min_length=1)


class ProvisionalArchitectureQ2V2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved", "manual_review"]
    minimum_coherent_pages: int | None = Field(default=None, ge=1)
    proposed_page_roles: list[ProvisionalPageRoleQ2V2] = Field(default_factory=list)
    semantic_needs: list[str]
    reasoning_codes: list[str]
    unresolved_requirements: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_resolution(self):
        if self.status == "resolved":
            if self.minimum_coherent_pages is None:
                raise ValueError("Resolved architecture requires a page count")
            if len(self.proposed_page_roles) != self.minimum_coherent_pages:
                raise ValueError("Resolved page roles must match the provisional page count")
            if self.unresolved_requirements:
                raise ValueError("Resolved architecture cannot retain unresolved requirements")
        else:
            self.minimum_coherent_pages = None
            self.proposed_page_roles = []
            if not self.unresolved_requirements:
                raise ValueError("Manual review requires an unresolved reason")
        return self


class ScopeQualificationQ2V2(BaseModel):
    rule_version: Literal["ireland_accepted_v1"]
    eligibility: Literal["supported", "unsupported", "manual_review"]
    starting_package: Literal["starter", "business", "reference", "advanced"] | None
    minimum_package: Literal["starter", "business", "reference", "advanced"] | None
    recommended_package: Literal["starter", "business", "reference", "advanced"] | None
    required_floor_reasons: list[ScopeReasonQ2V2]
    optional_recommendation_reasons: list[ScopeReasonQ2V2]
    provisional_architecture: ProvisionalArchitectureQ2V2
    provisional_page_need: int | None = Field(default=None, ge=1)
    commercial_paths: list[dict] = Field(default_factory=list)
    proposed_paid_pages: list[dict] = Field(default_factory=list)
    confirmed_paid_addons: list[PaidStructuralOptionQ2V2]
    unresolved_requirements: list[UnresolvedRequirementQ2V2]
    checkout_ready: bool


class Q2V2Response(BaseModel):
    order_id: str
    journey_id: str
    flow_version: str
    schema_version: int
    idempotent: bool
    scope_qualification: ScopeQualificationQ2V2
