-- =============================================================
-- Raina AI — MT5 tables migration
-- Run once in Supabase Dashboard → SQL Editor
-- =============================================================

-- MT5 account connections (persists across Railway redeploys)
CREATE TABLE IF NOT EXISTS public.mt5_accounts (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         TEXT        UNIQUE NOT NULL,   -- mt5_login (broker account number)
    api_key         TEXT        UNIQUE NOT NULL,   -- EA authentication key
    metaapi_id      TEXT,                          -- MetaAPI cloud account ID
    account_mode    TEXT        NOT NULL DEFAULT 'demo',
    is_connected    BOOLEAN     NOT NULL DEFAULT false,
    broker_name     TEXT,
    account_number  TEXT,
    balance         NUMERIC,
    equity          NUMERIC,
    last_heartbeat  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Per-user risk / scalping settings (JSON blob for flexibility)
CREATE TABLE IF NOT EXISTS public.mt5_settings (
    user_id         TEXT        PRIMARY KEY,
    settings_json   TEXT        NOT NULL DEFAULT '{}'
);

-- Trade orders: pending → sent → open → closed/failed/cancelled
CREATE TABLE IF NOT EXISTS public.mt5_trades (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         TEXT        NOT NULL,
    api_key         TEXT        NOT NULL,
    asset           TEXT        NOT NULL,
    direction       TEXT        NOT NULL,          -- BUY | SELL
    lot_size        NUMERIC     NOT NULL,
    entry_price     NUMERIC,
    stop_loss       NUMERIC,
    take_profit     NUMERIC,
    confidence      NUMERIC,
    timeframe       TEXT,
    status          TEXT        NOT NULL DEFAULT 'pending',
    mt5_ticket      BIGINT,
    open_price      NUMERIC,
    close_price     NUMERIC,
    profit          NUMERIC,
    comment         TEXT        DEFAULT 'RainX',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opened_at       TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_mt5_accounts_uid   ON public.mt5_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_mt5_accounts_key   ON public.mt5_accounts(api_key);
CREATE INDEX IF NOT EXISTS idx_mt5_accounts_meta  ON public.mt5_accounts(metaapi_id);
CREATE INDEX IF NOT EXISTS idx_mt5_trades_uid     ON public.mt5_trades(user_id, status);
CREATE INDEX IF NOT EXISTS idx_mt5_trades_key     ON public.mt5_trades(api_key, status);
CREATE INDEX IF NOT EXISTS idx_mt5_settings_uid   ON public.mt5_settings(user_id);

-- Enable Row Level Security (service role key bypasses RLS)
ALTER TABLE public.mt5_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mt5_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mt5_trades   ENABLE ROW LEVEL SECURITY;

-- Service role has full access (backend only — no client-side access)
CREATE POLICY "service_role_all" ON public.mt5_accounts FOR ALL USING (true);
CREATE POLICY "service_role_all" ON public.mt5_settings FOR ALL USING (true);
CREATE POLICY "service_role_all" ON public.mt5_trades   FOR ALL USING (true);

-- Done
SELECT 'MT5 tables created successfully' AS result;
