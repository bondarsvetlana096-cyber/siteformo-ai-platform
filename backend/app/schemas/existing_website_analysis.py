from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confidence(str, Enum):
    HIGH = "HIGH"; MEDIUM = "MEDIUM"; LOW = "LOW"; INCONCLUSIVE = "INCONCLUSIVE"


class ObservationState(str, Enum):
    OBSERVED = "OBSERVED"; LIKELY = "LIKELY"; NOT_OBSERVED = "NOT_OBSERVED"; UNKNOWN = "UNKNOWN"


class FailureCode(str, Enum):
    INVALID_URL="INVALID_URL"; UNSAFE_URL="UNSAFE_URL"; DNS_FAILURE="DNS_FAILURE"
    CONNECT_TIMEOUT="CONNECT_TIMEOUT"; READ_TIMEOUT="READ_TIMEOUT"; TLS_ERROR="TLS_ERROR"
    UNSAFE_REDIRECT="UNSAFE_REDIRECT"; TOO_MANY_REDIRECTS="TOO_MANY_REDIRECTS"
    ACCESS_RESTRICTED="ACCESS_RESTRICTED"; RATE_LIMITED_REMOTE="RATE_LIMITED_REMOTE"
    NON_HTML="NON_HTML"; OVERSIZED_RESPONSE="OVERSIZED_RESPONSE"; MALFORMED_CONTENT="MALFORMED_CONTENT"
    JS_HEAVY_INCONCLUSIVE="JS_HEAVY_INCONCLUSIVE"; PARTIAL_CRAWL="PARTIAL_CRAWL"; INTERNAL_FAILURE="INTERNAL_FAILURE"


class EvidenceRecordV2(ClosedModel):
    evidence_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    source_page_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(max_length=500)
    source_page_role: Literal["HOME","ABOUT","SERVICES","PRODUCT","CONTACT","BOOKING","ACCOUNT","BLOG","LEGAL","OTHER","UNKNOWN"]
    observation_type: Literal["ELEMENT","LINK","FORM","STRUCTURED_DATA","METADATA","TEXT_LABEL","ROUTE","PLATFORM_SIGNATURE","COUNT"]
    observed_value: bool | int | str
    confidence: Confidence
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CandidateV2(ClosedModel):
    value: str = Field(min_length=1, max_length=500)
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    confirmation_state: Literal["OBSERVED_ONLY","REQUIRES_CUSTOMER_CONFIRMATION"] = "REQUIRES_CUSTOMER_CONFIRMATION"


class BusinessEvidenceV2(ClosedModel):
    brand_names: list[CandidateV2] = Field(default_factory=list, max_length=5)
    page_title: CandidateV2 | None = None
    meta_description: CandidateV2 | None = None
    activity_candidates: list[CandidateV2] = Field(default_factory=list, max_length=10)
    service_categories: list[CandidateV2] = Field(default_factory=list, max_length=30)
    product_categories: list[CandidateV2] = Field(default_factory=list, max_length=30)
    published_locations: list[CandidateV2] = Field(default_factory=list, max_length=10)
    published_service_areas: list[CandidateV2] = Field(default_factory=list, max_length=10)
    public_contacts: list[CandidateV2] = Field(default_factory=list, max_length=10)
    social_links: list[CandidateV2] = Field(default_factory=list, max_length=20)
    languages: list[CandidateV2] = Field(default_factory=list, max_length=10)
    structured_organization_types: list[CandidateV2] = Field(default_factory=list, max_length=10)


class CrawlSummaryV2(ClosedModel):
    sitemap_status: Literal["FOUND","ABSENT","UNAVAILABLE","UNSAFE","NOT_CHECKED"]
    discovered_page_count: int = Field(ge=0, le=200)
    verified_crawled_pages: int = Field(ge=0, le=12)
    estimated_site_scale: Literal["SINGLE_PAGE","SMALL","MEDIUM","LARGE_OR_TRUNCATED","INCONCLUSIVE"]
    maximum_observed_depth: int = Field(ge=0, le=2)
    crawl_truncated: bool
    fetched_paths: list[str] = Field(default_factory=list, max_length=12)


class StructureEvidenceV2(ClosedModel):
    navigation_destinations: list[CandidateV2] = Field(default_factory=list, max_length=50)
    page_roles: list[CandidateV2] = Field(default_factory=list, max_length=30)
    major_content_categories: list[CandidateV2] = Field(default_factory=list, max_length=30)
    template_group_count: int = Field(ge=0, le=12)
    header_observed: bool
    footer_observed: bool
    navigation_observed: bool


FUNCTION_KEYS = Literal["contact_form","enquiry_form","newsletter_signup","booking_reservation","catalogue","transactional_ecommerce","cart","checkout","product_search","product_filters","site_search","account_login","registration","membership","subscription","file_upload","online_payment","map_location","chat_widget","multilingual_controls","gallery_portfolio","blog_editorial","reviews_testimonials","calculator_configurator","dashboard","customer_portal","saas_application","marketplace_multi_vendor","user_generated_content","api_integration_indicator"]


class FunctionObservationV2(ClosedModel):
    function_key: FUNCTION_KEYS
    observation: ObservationState
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    clarification_recommended: bool = False


class ContentEvidenceV2(ClosedModel):
    headings: list[CandidateV2] = Field(default_factory=list, max_length=20)
    service_names: list[CandidateV2] = Field(default_factory=list, max_length=30)
    product_categories: list[CandidateV2] = Field(default_factory=list, max_length=30)
    short_descriptions: list[CandidateV2] = Field(default_factory=list, max_length=10)
    faq_questions: list[CandidateV2] = Field(default_factory=list, max_length=10)
    cta_labels: list[CandidateV2] = Field(default_factory=list, max_length=20)
    navigation_labels: list[CandidateV2] = Field(default_factory=list, max_length=20)
    legal_page_presence: list[CandidateV2] = Field(default_factory=list, max_length=10)
    about_summary_candidates: list[CandidateV2] = Field(default_factory=list, max_length=5)
    public_contact_candidates: list[CandidateV2] = Field(default_factory=list, max_length=10)
    social_profiles: list[CandidateV2] = Field(default_factory=list, max_length=20)


class MaterialCandidateV2(ClosedModel):
    material_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["LOGO","FAVICON","HERO_IMAGE","SERVICE_IMAGE","PRODUCT_IMAGE","GALLERY_IMAGE","VIDEO_EMBED","SOCIAL_PROFILE","DOCUMENT"]
    source_page_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference: str = Field(max_length=1000)
    reuse_authority: Literal["UNCONFIRMED"] = "UNCONFIRMED"
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class MaterialEvidenceV2(ClosedModel):
    candidates: list[MaterialCandidateV2] = Field(default_factory=list, max_length=50)


class TechnologyObservationV2(ClosedModel):
    technology: Literal["wordpress","woocommerce","shopify","webflow","wix","squarespace","framer","elementor","divi","other_detected","unknown"]
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    migration_relevance: bool


class ComplexityBandV2(ClosedModel):
    band: Literal["LOW","MEDIUM","HIGH","INCONCLUSIVE"]
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class ComplexityEvidenceV2(ClosedModel):
    structural: ComplexityBandV2
    functional: ComplexityBandV2
    content: ComplexityBandV2
    visual: ComplexityBandV2


class ExistingWebsiteAnalysisV2(ClosedModel):
    contract_version: Literal["existing_website_analysis_v2"] = "existing_website_analysis_v2"
    analyzer_version: str
    fetch_policy_version: str
    analysis_id: str
    order_id: str
    status: Literal["COMPLETE","PARTIAL","UNAVAILABLE","REFUSED_UNSAFE","INVALID"]
    input_url_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_url: str | None = None
    started_at: datetime
    completed_at: datetime
    safe_failure_code: FailureCode | None = None
    confidence: Confidence
    crawl: CrawlSummaryV2
    business: BusinessEvidenceV2
    structure: StructureEvidenceV2
    functions: list[FunctionObservationV2]
    content: ContentEvidenceV2
    materials: MaterialEvidenceV2
    technologies: list[TechnologyObservationV2]
    complexity: ComplexityEvidenceV2
    clarification_flags: list[Literal["CONFIRM_EXISTING_ECOMMERCE_REQUIRED","CONFIRM_EXISTING_BOOKING_REQUIRED","CONFIRM_EXISTING_ACCOUNT_REQUIRED","CONFIRM_EXISTING_PORTAL_REQUIRED","CONFIRM_EXISTING_MULTILINGUAL_REQUIRED","CONFIRM_EXISTING_MARKETPLACE_REQUIRED","CONFIRM_EXISTING_PLATFORM_REQUIRED","CONFIRM_CONTENT_MIGRATION_SCOPE","CONFIRM_MATERIAL_REUSE_RIGHTS"]]
    evidence: list[EvidenceRecordV2] = Field(max_length=200)


class ExistingWebsiteAnalysisRequestV2(ClosedModel):
    url: str = Field(min_length=3, max_length=500)
