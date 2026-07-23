-- Stock Agent cloud portfolio schema for Supabase PostgreSQL.
-- Run this entire file once in the Supabase SQL Editor.

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.portfolios (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  name text not null check (char_length(trim(name)) between 1 and 80),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists portfolios_owner_name_unique
  on public.portfolios (owner_id, lower(name));

create table if not exists public.purchases (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  portfolio_id uuid not null references public.portfolios(id) on delete cascade,
  security_name text,
  ticker text check (ticker is null or char_length(ticker) between 1 and 16),
  quantity numeric check (quantity is null or quantity > 0),
  purchase_price numeric check (purchase_price is null or purchase_price >= 0),
  purchased_at date,
  note text not null default '',
  status text not null default 'draft' check (status in ('draft', 'complete')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists purchases_portfolio_id_idx
  on public.purchases (portfolio_id);
create index if not exists purchases_owner_id_idx
  on public.purchases (owner_id);
create index if not exists purchases_ticker_idx
  on public.purchases (ticker);

alter table public.portfolios enable row level security;
alter table public.purchases enable row level security;

-- Re-running this file is safe.
drop policy if exists "Users can view their portfolios" on public.portfolios;
drop policy if exists "Users can create their portfolios" on public.portfolios;
drop policy if exists "Users can update their portfolios" on public.portfolios;
drop policy if exists "Users can delete their portfolios" on public.portfolios;

create policy "Users can view their portfolios"
  on public.portfolios for select
  using (owner_id = auth.uid());
create policy "Users can create their portfolios"
  on public.portfolios for insert
  with check (owner_id = auth.uid());
create policy "Users can update their portfolios"
  on public.portfolios for update
  using (owner_id = auth.uid())
  with check (owner_id = auth.uid());
create policy "Users can delete their portfolios"
  on public.portfolios for delete
  using (owner_id = auth.uid());

drop policy if exists "Users can view their purchases" on public.purchases;
drop policy if exists "Users can create their purchases" on public.purchases;
drop policy if exists "Users can update their purchases" on public.purchases;
drop policy if exists "Users can delete their purchases" on public.purchases;

create policy "Users can view their purchases"
  on public.purchases for select
  using (
    owner_id = auth.uid()
    and exists (
      select 1 from public.portfolios p
      where p.id = portfolio_id and p.owner_id = auth.uid()
    )
  );
create policy "Users can create their purchases"
  on public.purchases for insert
  with check (
    owner_id = auth.uid()
    and exists (
      select 1 from public.portfolios p
      where p.id = portfolio_id and p.owner_id = auth.uid()
    )
  );
create policy "Users can update their purchases"
  on public.purchases for update
  using (owner_id = auth.uid())
  with check (
    owner_id = auth.uid()
    and exists (
      select 1 from public.portfolios p
      where p.id = portfolio_id and p.owner_id = auth.uid()
    )
  );
create policy "Users can delete their purchases"
  on public.purchases for delete
  using (owner_id = auth.uid());

drop trigger if exists portfolios_set_updated_at on public.portfolios;
create trigger portfolios_set_updated_at
before update on public.portfolios
for each row execute function public.set_updated_at();

drop trigger if exists purchases_set_updated_at on public.purchases;
create trigger purchases_set_updated_at
before update on public.purchases
for each row execute function public.set_updated_at();
