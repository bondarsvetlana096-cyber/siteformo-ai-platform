create extension if not exists pgcrypto;

create table if not exists orders (
  id uuid primary key default gen_random_uuid(),
  email text,
  plan text,
  status text default 'PENDING_PAYMENT',
  amount integer,
  deposit integer,
  extended_brief jsonb,
  design_previews jsonb,
  created_at timestamptz default now()
);

create table if not exists client_profiles (
  id uuid primary key default gen_random_uuid(),
  order_id uuid references orders(id) on delete cascade,
  email text,
  name text,
  created_at timestamptz default now()
);
create table if not exists design_briefs (
  id uuid primary key default gen_random_uuid(),
  order_id uuid references orders(id) on delete cascade,
  brief jsonb,
  created_at timestamptz default now()
);
create table if not exists design_previews (
  id uuid primary key default gen_random_uuid(),
  order_id uuid references orders(id) on delete cascade,
  preview jsonb,
  created_at timestamptz default now()
);select * from orders;
select * from client_profiles;
select * from design_briefs;