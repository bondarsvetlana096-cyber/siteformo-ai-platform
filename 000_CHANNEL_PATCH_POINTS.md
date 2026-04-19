# Channel patch points

WhatsApp and Facebook Messenger are intentionally disabled in this build.
When those channels are ready, apply patches in the following files:

- `backend/app/models/request.py`
  - restore new `ContactType` values for `whatsapp` and `messenger`
- `backend/app/schemas/request.py`
  - extend `CreateRequestPayload.contact_type` literals
  - add validation rules for the restored channels
- `backend/app/core/config.py`
  - restore channel-specific settings such as contact URL, labels, or phone number
- `backend/app/services/request_service.py`
  - restore channel-specific contact normalization rules
- `backend/app/services/messaging_links.py`
  - add initial message templates
  - add result message delivery helpers
  - add confirmation-link builders
  - add channel label resolution
- `backend/app/services/followups.py`
  - add channel-specific outbound follow-up hints
- `backend/.env.example`
  - restore the related environment variables
- `backend/.env.local.example`
  - restore the related environment variables
- `backend/.env`
  - restore the related environment variables if you keep this file locally
- `backend/README.md`
  - document the restored channels and their env variables
- `project logic.md`
  - restore the product-spec references for the returned channels

Recommended env names for a future patch:
- `WHATSAPP_CONTACT_NUMBER`
- `MESSENGER_CONTACT_URL`
- `MESSENGER_CONTACT_LABEL`
