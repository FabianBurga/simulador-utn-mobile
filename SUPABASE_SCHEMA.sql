-- P2 MOBILE RC1 - SUPABASE SCHEMA
-- Ejecutar UNA sola vez en Supabase > SQL Editor.

create table if not exists public.p2_mobile_users (
    id uuid primary key,
    user_code text not null unique,
    pin_salt text not null,
    pin_hash text not null,
    history_json jsonb not null default '[]'::jsonb,
    state_json jsonb null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists p2_mobile_users_user_code_idx
on public.p2_mobile_users (user_code);

alter table public.p2_mobile_users enable row level security;

-- No se crean politicas publicas.
-- Streamlit accede exclusivamente desde servidor mediante una Secret Key.
