create extension if not exists pgmq;

select pgmq.create('generate_demo_queue');
select pgmq.create('expire_demo_queue');
select pgmq.create('follow_up_queue');
select pgmq.create('cleanup_demo_queue');

create or replace function public.pgmq_send(queue_name text, msg jsonb, delay integer default 0)
returns bigint
language sql
security definer
as $$
  select pgmq.send(queue_name, msg, delay => delay);
$$;

create or replace function public.pgmq_read(queue_name text, vt integer default 60, qty integer default 1)
returns table(msg_id bigint, read_ct integer, enqueued_at timestamptz, vt timestamptz, message jsonb)
language sql
security definer
as $$
  select * from pgmq.read(queue_name, vt, qty);
$$;

create or replace function public.pgmq_delete(queue_name text, msg_id bigint)
returns boolean
language sql
security definer
as $$
  select pgmq.delete(queue_name, msg_id);
$$;

grant execute on function public.pgmq_send(text, jsonb, integer) to authenticated, service_role;
grant execute on function public.pgmq_read(text, integer, integer) to authenticated, service_role;
grant execute on function public.pgmq_delete(text, bigint) to authenticated, service_role;
