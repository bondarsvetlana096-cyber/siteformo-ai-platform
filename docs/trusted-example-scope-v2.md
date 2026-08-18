# Trusted Example Scope V2

Contact delivery quota is permanently defined as two accepted attempts per channel per
Example and protected contact identity. The generic key contract is:

`namespace + channel + trusted_example_id_hash + protected_contact_identity`

## Canonical identities

- Business 01: `SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1`
- Business 02 / VOLTINK: `SF_BU_02_VOLTINK_EXAMPLE_V1`
- Business 03 / NORTHFORM: `SF_BU_03_NORTHFORM_EXAMPLE_V1`

These identifiers are stable product identities and do not contain the temporary dev host.

## Trust resolution

All Contact request models explicitly accept `example_id` and retain Pydantic
`extra="forbid"`. The shared resolver combines the exact `Origin` header with the requested
canonical identity. `https://dev.siteformo.com` may request any of the three allowlisted
Examples. Dedicated `businessN.siteformo.com` origins are restricted to their matching
identity. Unknown origins, unknown identifiers, and mismatched Origin/Example pairs fail
closed before quota state or provider access.

The only shared-dev compatibility fallback maps a missing `example_id` to Business 01. It is
bounded, explicit and exists only for the original legacy caller. Dedicated public origins
may omit the field because the origin uniquely identifies the Example. Business 02 and 03
callers on the shared dev origin send their canonical identity explicitly.

## Channel keys and migration

- Email remains `sf:demo-email:v1:quota:EMAIL:<example_hash>:<contact_hash>`.
- SMS remains `sf:demo-sms:v1:quota:SMS:<role>:<example_hash>:<contact_hash>`.
- Call changes from `sf:demo-voice:v1:quota:recipient:<contact_hash>` to
  `sf:demo-voice:v1:quota:CALL:<example_hash>:<contact_hash>`.

Existing Redis records are neither renamed nor deleted. Historical Business 01 Email/SMS
records continue to apply to Business 01. Business 02 and 03 naturally receive distinct
hashes. Legacy unscoped Call keys remain inert historical records; new scoped keys start
independent counters. Rate and global safety limits remain separate safeguards.

Future WhatsApp, Viber, Messenger, Telegram and other channels must call the same resolver
and use the generic key contract. Provider-specific Example mappings are not permitted.

HTTP 429 retains canonical machine-readable details. Frontends render quota exhaustion as a
contact-method limit and rate limiting as a temporary retry message; Redis and provider
details are not exposed.

## Persistent per-Example per-Channel Quota

The permanent allowance is a maximum of two accepted actions for each combination of
trusted Example, active channel, and protected normalized contact identity. Page reloads,
navigation, tab or browser closure, and later return visits do not create a new allowance.
Quota counters are durable Redis records with no automatic expiry; their expected TTL is
`-1`. Existing records are not renamed, reset, or deleted.

Examples are isolated: exhausting Email on Business 03 does not consume Email allowance on
Business 01 or Business 02. Channels are also isolated: exhausting Call does not consume
SMS or Email allowance in the same Example. Temporary client rate-limit keys retain their
short expiry and can reset without altering persistent quota counters.

Every current and future channel must use the generic contract:

`channel namespace + trusted_example_id_hash + protected_contact_identity_hash`

Email identities are normalized lowercase addresses before hashing. Telephone channels use
normalized E.164 identities before hashing. Raw contact values must never appear in quota
key names. WhatsApp, Telegram, Viber, Messenger, and future channels extend this contract by
using their own channel namespace; they do not introduce a shared or session-scoped quota.
