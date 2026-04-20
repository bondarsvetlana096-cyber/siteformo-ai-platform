# Fixes applied (UPDATED)

## Files changed
- backend/app/core/config.py
- backend/app/services/queue.py
- backend/app/services/publisher.py
- backend/app/services/turnstile.py
- backend/app/services/request_service.py

## What was fixed

### 1. Config fixes
- Restored backward-compatible Settings model
- Added database_url and env compatibility
- Normalized APP_ENV values

### 2. Worker & Queue
- Fixed queue enqueue fallback
- Worker now correctly processes jobs

### 3. Turnstile
- Added dev bypass:
  APP_ENV=local / development

### 4. Publish & Storage
- Fixed TTL and retention logic
- master_storage_key now stored in demo_assets.storage_key
- retention_expires_at stored in demo_assets.expires_at

### 5. CRITICAL FIX — Duplicate publish

#### Problem
One request_id produced:
- multiple demo_published events
- duplicate demo_assets rows

#### Solution

##### Code fix (request_service.py)

Added idempotency guard:

if existing_asset:
    return

Before:
- publish_demo()
- insert DemoAsset
- log_event demo_published

##### DB fix

Removed duplicates:

delete from public.demo_assets a
using public.demo_assets b
where a.id < b.id
  and a.request_id = b.request_id
  and a.asset_type = b.asset_type
  and a.deleted_at is null
  and b.deleted_at is null;

Added unique constraint:

create unique index uq_demo_assets_request_asset_type
on public.demo_assets (request_id, asset_type)
where deleted_at is null;

## Final pipeline

API → queue → worker → generate → publish → DB

## Data mapping

- master_storage_key → demo_assets.storage_key
- retention_expires_at → demo_assets.expires_at
- demo_url → event_logs.payload

## Verification

No duplicates:

select
  request_id,
  asset_type,
  count(*) as cnt
from public.demo_assets
where deleted_at is null
group by request_id, asset_type
having count(*) > 1;

Result: 0 rows

## Final status

✔ API works  
✔ Worker works  
✔ Queue works  
✔ Generation works  
✔ Publish works  
✔ No duplicates  
✔ System is idempotent
