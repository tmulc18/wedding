-- Wedding RSVP schema for sep19.party
-- Run once via the Supabase SQL editor. Safe to re-run: tables use IF NOT EXISTS,
-- functions use CREATE OR REPLACE.

-- Tables -------------------------------------------------------------
create table if not exists guest_list (
  phone        text primary key,           -- E.164, e.g. +14155550123 (primary phone for the party)
  guests       text[]      not null default '{}',
  num_allowed  int         not null check (num_allowed > 0),
  created_at   timestamptz not null default now()
);

-- Additional phones that resolve to the same party. Lets either member of a
-- household look up their invite with their own number.
create table if not exists phone_aliases (
  alias_phone    text primary key,         -- E.164
  primary_phone  text not null references guest_list(phone) on delete cascade,
  created_at     timestamptz not null default now()
);
create index if not exists phone_aliases_primary_idx on phone_aliases(primary_phone);

-- One row per guest, holding a yes/no for each of the two events.
create table if not exists rsvps (
  id                uuid primary key default gen_random_uuid(),
  phone             text        not null references guest_list(phone) on delete cascade,
  guest_name        text        not null,
  attending_friday  boolean     not null, -- Fri Sept 18, drinks + bites
  attending         boolean     not null, -- Sat Sept 19, reception (the headline count)
  dietary           text,                 -- free-text restrictions/allergies; null when none given
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (phone, guest_name)
);

-- For tables created before these columns existed. The default only backfills
-- existing rows; every new row gets an explicit value from submit_rsvp.
alter table rsvps add column if not exists dietary          text;
alter table rsvps add column if not exists attending_friday boolean not null default false;

-- RLS: deny everything by default. Only the security-definer RPCs touch data.
alter table guest_list    enable row level security;
alter table phone_aliases enable row level security;
alter table rsvps         enable row level security;

-- Resolve a possibly-alias phone to the primary phone in guest_list.
-- Returns NULL when the phone is unknown.
create or replace function resolve_phone(p_phone text)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    (select phone from guest_list where phone = p_phone),
    (select primary_phone from phone_aliases where alias_phone = p_phone)
  );
$$;

revoke all on function resolve_phone(text) from public;
grant  execute on function resolve_phone(text) to anon, authenticated;

-- RPC: lookup --------------------------------------------------------
create or replace function lookup_guest(p_phone text)
returns table(phone text, guests text[], num_allowed int)
language sql
security definer
set search_path = public
as $$
  select gl.phone, gl.guests, gl.num_allowed
  from guest_list gl
  where gl.phone = resolve_phone(p_phone);
$$;

revoke all on function lookup_guest(text) from public;
grant  execute on function lookup_guest(text) to anon, authenticated;

-- RPC: lookup existing responses ------------------------------------
-- Returns the party's previously-submitted RSVP rows (empty if none yet),
-- so a returning guest sees and can edit their prior selections.
-- The return type has changed as columns were added, and CREATE OR REPLACE
-- cannot change a function's return type — drop first so re-runs succeed.
drop function if exists lookup_rsvp(text);

create or replace function lookup_rsvp(p_phone text)
returns table(guest_name text, attending boolean, attending_friday boolean, dietary text)
language sql
security definer
set search_path = public
as $$
  select rs.guest_name, rs.attending, rs.attending_friday, rs.dietary
  from rsvps rs
  where rs.phone = resolve_phone(p_phone)
  order by rs.created_at;
$$;

revoke all on function lookup_rsvp(text) from public;
grant  execute on function lookup_rsvp(text) to anon, authenticated;

-- RPC: submit --------------------------------------------------------
-- p_responses: jsonb array of
--   {name: text, attending: bool, attending_friday: bool, dietary: text?}
-- Replaces all prior responses for this phone atomically.
create or replace function submit_rsvp(p_phone text, p_responses jsonb)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_phone   text;
  v_allowed int;
  r         jsonb;
  v_name    text;
begin
  v_phone := resolve_phone(p_phone);

  if v_phone is null then
    raise exception 'unknown phone';
  end if;

  select num_allowed into v_allowed
    from guest_list
   where phone = v_phone;

  if jsonb_typeof(p_responses) <> 'array' then
    raise exception 'p_responses must be a JSON array';
  end if;

  if jsonb_array_length(p_responses) = 0 then
    raise exception 'no responses provided';
  end if;

  if jsonb_array_length(p_responses) > v_allowed then
    raise exception 'too many responses (max %)', v_allowed;
  end if;

  delete from rsvps where phone = v_phone;

  for r in select * from jsonb_array_elements(p_responses) loop
    v_name := trim(r->>'name');
    if v_name is null or v_name = '' then
      raise exception 'each response needs a non-empty name';
    end if;
    insert into rsvps(phone, guest_name, attending, attending_friday, dietary)
    values (
      v_phone,
      v_name,
      (r->>'attending')::boolean,
      coalesce((r->>'attending_friday')::boolean, false),
      nullif(trim(coalesce(r->>'dietary', '')), '')
    )
    on conflict (phone, guest_name) do update
      set attending         = excluded.attending,
          attending_friday  = excluded.attending_friday,
          dietary           = excluded.dietary,
          updated_at        = now();
  end loop;
end;
$$;

revoke all on function submit_rsvp(text, jsonb) from public;
grant  execute on function submit_rsvp(text, jsonb) to anon, authenticated;
