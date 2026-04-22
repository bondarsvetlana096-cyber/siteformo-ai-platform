insert into storage.buckets (id, name, public)
values ('siteformo-demo', 'siteformo-demo', false)
on conflict (id) do nothing;

create index if not exists idx_requests_demo_token on public.requests (demo_token);
create index if not exists idx_requests_status on public.requests (status);
create index if not exists idx_requests_expires_at on public.requests (expires_at);
create index if not exists idx_demo_assets_request_id on public.demo_assets (request_id);
create index if not exists idx_demo_assets_expires_at on public.demo_assets (expires_at);
create index if not exists idx_requests_retention_expires_at on public.requests (retention_expires_at);
