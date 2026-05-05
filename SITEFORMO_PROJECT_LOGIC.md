# SiteFormo Project Logic - Generation Quality Pipeline

This file is the source of truth for the current SiteFormo automation logic.

## Current position

The project already has:

- quiz
- Stripe deposit
- extended questionnaire
- generation job queue
- first design preview generation
- `design_previews`, `logo_previews`, `preview_generation_payload` fields on orders

The missing layer is quality control after the first generated result.

## Correct pipeline

```text
Client submits extended brief
↓
Order status = BRIEF_SUBMITTED
↓
Generation job = DESIGN_PREVIEWS / PENDING
↓
Worker generates first design previews
↓
Quality pipeline runs automatically
↓
Technical check
↓
AI quality review
↓
Auto-improvement pass if needed
↓
Repeat until package limit
↓
Persist quality result to DB
↓
If READY_TO_SEND → send preview email
If MANUAL_REVIEW_REQUIRED → do not email client, owner checks manually
```

## Package quality rules

```text
Starter:
- target_score: 7.5
- max_quality_iterations: 1
- max_warnings: 5

Business:
- target_score: 8.0
- max_quality_iterations: 2
- max_warnings: 3

Premium:
- target_score: 8.5
- max_quality_iterations: 3
- max_warnings: 2

Custom:
- target_score: 9.0
- max_quality_iterations: 5
- max_warnings: 1
```

## Status logic

### READY_TO_SEND
A design can be emailed to the client only when:

- technical check passed
- AI review score is >= package target score
- no critical errors exist
- warnings are within package limit

### MANUAL_REVIEW_REQUIRED
The system must stop before emailing the client when:

- no preview reaches target score
- technical check fails after max attempts
- AI review finds critical errors
- max quality iterations are reached

## Where quality results are stored

No new DB migration is required for this version.

Quality result is stored in:

```text
orders.preview_generation_payload.quality_pipeline
```

The checked previews are stored back into:

```text
orders.design_previews
```

Each preview receives:

```json
{
  "quality_report": {
    "status": "READY_TO_SEND | NEEDS_REGENERATION | NEEDS_FIX",
    "overall_score": 8.4,
    "ready_to_send": true,
    "critical_errors": [],
    "warnings": [],
    "history": []
  }
}
```

## Order fields updated

```text
order.design_previews
order.preview_generation_payload
order.generation_status
order.design_status
order.status
```

## Email rule

Client preview email must be sent only when:

```text
quality_pipeline.status == READY_TO_SEND
```

If status is:

```text
MANUAL_REVIEW_REQUIRED
```

then the worker does not send email.

## New service files

```text
backend/app/services/quality_package_rules.py
backend/app/services/technical_check_service.py
backend/app/services/quality_review_service.py
backend/app/services/auto_improvement_service.py
backend/app/services/pre_delivery_check_service.py
backend/app/services/pipeline_result_service.py
backend/app/services/design_quality_pipeline_service.py
```

## Patched worker files

```text
backend/app/workers/worker.py
backend/app/workers/generation_worker.py
```

Both workers now:

1. generate previews
2. run quality pipeline
3. save quality results to DB
4. email the client only if READY_TO_SEND

## Important rule

Never send first generation directly to the client.

Always:

```text
generate → check → improve → save result → decide next action
```
