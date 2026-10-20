-- Gerador de DDS: isolated multi-organization access schema.
-- Applied first to dedicated project iqxdkmcwztcpdzvdnsts (not Missão em Dia).
begin;
create extension if not exists pgcrypto;
create table if not exists public.organizations (
 id uuid primary key default gen_random_uuid(),
 name text not null check (length(trim(name)) between 2 and 120),
 status text not null default 'active' check (status in ('active','suspended','expired')),
 created_at timestamptz not null default now(),
 expires_at timestamptz
);
create table if not exists public.organization_members (
 organization_id uuid not null references public.organizations(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade,
 role text not null default 'member' check (role in ('owner','admin','member','viewer')),
 status text not null default 'active' check (status in ('active','revoked')),
 created_at timestamptz not null default now(),
 primary key (organization_id,user_id)
);
create table if not exists public.organization_invites (
 id uuid primary key default gen_random_uuid(),
 organization_id uuid not null references public.organizations(id) on delete cascade,
 email text not null check (email=lower(trim(email))),
 role text not null default 'member' check (role in ('admin','member','viewer')),
 token_hash text not null unique,
 created_by uuid not null references auth.users(id),
 created_at timestamptz not null default now(),
 expires_at timestamptz not null,
 accepted_at timestamptz,
 revoked_at timestamptz,
 check (expires_at>created_at)
);
create table if not exists public.organization_audit_events (
 id bigint generated always as identity primary key,
 organization_id uuid references public.organizations(id) on delete set null,
 actor_user_id uuid references auth.users(id) on delete set null,
 event_type text not null check (length(event_type) between 1 and 100),
 target_email text,
 metadata jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);
create index if not exists organization_members_user_status_idx on public.organization_members(user_id,status);
create index if not exists organization_invites_org_email_idx on public.organization_invites(organization_id,email);
create index if not exists organization_invites_expiry_idx on public.organization_invites(expires_at) where accepted_at is null and revoked_at is null;
create index if not exists organization_invites_created_by_idx on public.organization_invites(created_by);
create index if not exists organization_audit_org_created_idx on public.organization_audit_events(organization_id,created_at desc);
create index if not exists organization_audit_actor_idx on public.organization_audit_events(actor_user_id);
create or replace function public.is_org_member(p_org uuid) returns boolean language sql stable security definer set search_path=public,auth as $$
 select exists(select 1 from public.organization_members m where m.organization_id=p_org and m.user_id=auth.uid() and m.status='active');
$$;
create or replace function public.is_org_admin(p_org uuid) returns boolean language sql stable security definer set search_path=public,auth as $$
 select exists(select 1 from public.organization_members m where m.organization_id=p_org and m.user_id=auth.uid() and m.status='active' and m.role in ('owner','admin'));
$$;
create or replace function public.accept_organization_invite(p_token_hash text) returns uuid language plpgsql security definer set search_path=public,auth as $$
declare v_invite public.organization_invites%rowtype; v_email text;
begin
 if auth.uid() is null then raise exception 'authentication_required' using errcode='28000'; end if;
 select lower(trim(email)) into v_email from auth.users where id=auth.uid();
 if v_email is null then raise exception 'user_email_missing' using errcode='28000'; end if;
 select * into v_invite from public.organization_invites where token_hash=p_token_hash for update;
 if not found then raise exception 'invalid_invite' using errcode='P0001'; end if;
 if v_invite.accepted_at is not null then raise exception 'invite_already_used' using errcode='P0001'; end if;
 if v_invite.revoked_at is not null then raise exception 'invite_revoked' using errcode='P0001'; end if;
 if v_invite.expires_at<=now() then raise exception 'invite_expired' using errcode='P0001'; end if;
 if v_invite.email<>v_email then raise exception 'invite_email_mismatch' using errcode='42501'; end if;
 update public.organization_invites set accepted_at=now() where id=v_invite.id;
 insert into public.organization_members(organization_id,user_id,role,status) values(v_invite.organization_id,auth.uid(),v_invite.role,'active')
 on conflict(organization_id,user_id) do update set role=excluded.role,status='active';
 insert into public.organization_audit_events(organization_id,actor_user_id,event_type,target_email,metadata)
 values(v_invite.organization_id,auth.uid(),'invite.accepted',v_email,jsonb_build_object('invite_id',v_invite.id));
 return v_invite.organization_id;
end; $$;
alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.organization_invites enable row level security;
alter table public.organization_audit_events enable row level security;
drop policy if exists organizations_read_member on public.organizations;
drop policy if exists organizations_update_admin on public.organizations;
drop policy if exists organizations_select_member on public.organizations;
drop policy if exists members_manage_admin on public.organization_members;
drop policy if exists members_read_same_org on public.organization_members;
drop policy if exists members_select_same_org on public.organization_members;
drop policy if exists invites_admin_only on public.organization_invites;
drop policy if exists invites_select_admin on public.organization_invites;
drop policy if exists audit_admin_insert on public.organization_audit_events;
drop policy if exists audit_admin_read on public.organization_audit_events;
drop policy if exists audit_select_admin on public.organization_audit_events;
create policy organizations_select_member on public.organizations for select to authenticated using ((select public.is_org_member(id)));
create policy members_select_same_org on public.organization_members for select to authenticated using ((select public.is_org_member(organization_id)));
create policy invites_select_admin on public.organization_invites for select to authenticated using ((select public.is_org_admin(organization_id)));
create policy audit_select_admin on public.organization_audit_events for select to authenticated using ((select public.is_org_admin(organization_id)));
revoke all on public.organizations,public.organization_members,public.organization_invites,public.organization_audit_events from anon,authenticated;
grant select on public.organizations,public.organization_members,public.organization_invites,public.organization_audit_events to authenticated;
revoke all on function public.is_org_member(uuid) from public,anon;
revoke all on function public.is_org_admin(uuid) from public,anon;
revoke all on function public.accept_organization_invite(text) from public,anon;
grant execute on function public.is_org_member(uuid),public.is_org_admin(uuid),public.accept_organization_invite(text) to authenticated;
commit;