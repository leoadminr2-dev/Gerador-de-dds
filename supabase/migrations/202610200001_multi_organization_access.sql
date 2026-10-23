-- Gerador de DDS: isolated multi-organization foundation
-- Apply ONLY to a dedicated development Supabase project, never to MISSÃO EM DIA.
create extension if not exists pgcrypto;

create table if not exists public.dds_organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 2 and 120),
  status text not null default 'active' check (status in ('active','suspended','expired')),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  created_by uuid not null references auth.users(id),
  check (expires_at > created_at)
);

create table if not exists public.dds_organization_members (
  organization_id uuid not null references public.dds_organizations(id) on delete restrict,
  user_id uuid not null references auth.users(id) on delete restrict,
  role text not null check (role in ('org_admin','member')),
  status text not null default 'active' check (status in ('active','revoked')),
  joined_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create table if not exists public.dds_organization_invites (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.dds_organizations(id) on delete restrict,
  email_normalized text not null check (email_normalized = lower(trim(email_normalized))),
  role text not null default 'member' check (role in ('org_admin','member')),
  token_hash text not null unique,
  invited_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  status text not null default 'pending' check (status in ('pending','accepted','revoked','expired')),
  accepted_by uuid references auth.users(id),
  accepted_at timestamptz,
  check (expires_at > created_at)
);
create unique index if not exists dds_one_pending_invite_per_email_org
  on public.dds_organization_invites(organization_id, email_normalized)
  where status = 'pending';

create table if not exists public.dds_audit_events (
  id bigint generated always as identity primary key,
  organization_id uuid references public.dds_organizations(id) on delete restrict,
  actor_user_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  outcome text not null check (outcome in ('success','denied','failed')),
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists dds_audit_org_time on public.dds_audit_events(organization_id, created_at desc);

create or replace function public.dds_is_active_member(p_org uuid)
returns boolean language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.dds_organization_members m
    join public.dds_organizations o on o.id=m.organization_id
    where m.organization_id=p_org and m.user_id=auth.uid()
      and m.status='active' and o.status='active' and o.expires_at > now()
  );
$$;

create or replace function public.dds_is_org_admin(p_org uuid)
returns boolean language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.dds_organization_members m
    join public.dds_organizations o on o.id=m.organization_id
    where m.organization_id=p_org and m.user_id=auth.uid()
      and m.status='active' and m.role='org_admin'
      and o.status='active' and o.expires_at > now()
  );
$$;

alter table public.dds_organizations enable row level security;
alter table public.dds_organization_members enable row level security;
alter table public.dds_organization_invites enable row level security;
alter table public.dds_audit_events enable row level security;

drop policy if exists dds_org_read_member on public.dds_organizations;
create policy dds_org_read_member on public.dds_organizations for select to authenticated
  using (public.dds_is_active_member(id));
drop policy if exists dds_org_admin_update on public.dds_organizations;
create policy dds_org_admin_update on public.dds_organizations for update to authenticated
  using (public.dds_is_org_admin(id)) with check (public.dds_is_org_admin(id));

drop policy if exists dds_members_read_admin on public.dds_organization_members;
create policy dds_members_read_admin on public.dds_organization_members for select to authenticated
  using (public.dds_is_org_admin(organization_id) or user_id=auth.uid());
drop policy if exists dds_members_admin_manage on public.dds_organization_members;
create policy dds_members_admin_manage on public.dds_organization_members for all to authenticated
  using (public.dds_is_org_admin(organization_id))
  with check (public.dds_is_org_admin(organization_id));

-- Invites and audit rows are intentionally inaccessible directly from the browser.
-- Trusted server-side Edge Functions using service role must validate invite tokens,
-- authenticated email, expiry, single use, organization status, and role before writes.
revoke all on public.dds_organization_invites from anon, authenticated;
revoke all on public.dds_audit_events from anon, authenticated;
grant select on public.dds_organizations, public.dds_organization_members to authenticated;
grant execute on function public.dds_is_active_member(uuid) to authenticated;
grant execute on function public.dds_is_org_admin(uuid) to authenticated;

-- Prevent accidental anonymous access. Server-side provisioning must create the
-- initial organization and first org_admin atomically through a trusted function.
