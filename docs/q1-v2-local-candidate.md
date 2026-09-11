# Q1 V2 local candidate

This candidate is local only. It does not change WordPress, Railway, environment variables, Stripe, Q2, Examples visual pages, or Assistant enablement.

## Identity

`SiteFormoVisitor` is the cross-funnel Journey identity. `Order` is one project. `SiteFormoJourneyProject` is the explicit one-visitor-to-many-projects binding; neither identifier substitutes for the other. The opaque `X-SiteFormo-Visitor` credential authorizes Q1 project creation and update. Contact, IP, fingerprint, and request IDs are not ownership credentials.

Examples browsing and selection are telemetry only and never create an Order. The explicit **Start** action on the Q1 intro calls `POST /api/orders/q1/project`: it resumes the one current `draft` Order or creates one. A binding is current only while `is_current=true`; completed/paid/cancelled/non-draft Orders are retired on the next project start. An explicit `start_new_project=true` retires the current binding and creates another Order. `PATCH /api/orders/{order_id}/q1` can only update the current draft bound to the requesting Journey and is idempotent for identical state.

## Package semantics

`package_browsing_context` records what was browsed. `package_qualification` may contain an `existing_website_analysis` candidate with `candidate_unconfirmed` status. Q1 V2 never serializes `qualified_package` and never changes pricing, page count, or the Order's recommended tier.

## Q2 compatibility debt

The adapter persists the canonical `order_id`, a safe legacy `project_type` mapping, operational contact fields, `current_website_url`, and the complete Q1 V2 state. It deliberately does not write legacy `plan`, `package_key`, or price values. Current Q2 may still fall back to its own package defaults; removing that behavior belongs to the separate Q2 audit.

## Sleeping Assistant

The Q1 launcher is a disabled placeholder and makes no Assistant session, history, message, or OpenAI calls. Its panel-open state and future draft field are local UI state. Scrolling collapses the panel without terminating or clearing state. Every screen exposes its stable `data-q1-step` value.
