-- ── Groups ──
CREATE TABLE IF NOT EXISTS wa_groups (
    jid TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    participant_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    is_monitored BOOLEAN DEFAULT true
);

-- ── Participants ──
CREATE TABLE IF NOT EXISTS wa_participants (
    id SERIAL PRIMARY KEY,
    group_jid TEXT NOT NULL REFERENCES wa_groups(jid),
    phone TEXT NOT NULL,
    push_name TEXT DEFAULT '',
    is_admin BOOLEAN DEFAULT false,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(group_jid, phone)
);

-- ── Messages (partitioned by month) ──
CREATE TABLE IF NOT EXISTS wa_messages (
    id TEXT NOT NULL,
    group_jid TEXT NOT NULL,
    sender_phone TEXT NOT NULL,
    sender_name TEXT DEFAULT '',
    message_type TEXT NOT NULL DEFAULT 'conversation',
    content TEXT DEFAULT '',
    content_search TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED,
    media_type TEXT,
    media_path TEXT,
    media_thumb_path TEXT,
    media_mime TEXT,
    media_size INTEGER,
    media_duration INTEGER,
    reply_to_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL,
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Current + next 3 months partitions
CREATE TABLE IF NOT EXISTS wa_messages_2026_10 PARTITION OF wa_messages
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS wa_messages_2026_11 PARTITION OF wa_messages
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS wa_messages_2026_12 PARTITION OF wa_messages
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS wa_messages_2027_01 PARTITION OF wa_messages
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wa_msg_group_ts ON wa_messages (group_jid, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_wa_msg_sender ON wa_messages (sender_phone, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_wa_msg_search ON wa_messages USING GIN (content_search);

-- ── AI Summary Cache ──
CREATE TABLE IF NOT EXISTS wa_daily_summaries (
    id SERIAL PRIMARY KEY,
    group_jid TEXT NOT NULL REFERENCES wa_groups(jid),
    summary_date DATE NOT NULL,
    message_count INTEGER DEFAULT 0,
    summary TEXT NOT NULL,
    model TEXT DEFAULT 'haiku',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(group_jid, summary_date)
);

-- ── Email Configuration ──
CREATE TABLE IF NOT EXISTS wa_email_config (
    id INTEGER PRIMARY KEY DEFAULT 1,
    smtp_host TEXT DEFAULT '',
    smtp_port INTEGER DEFAULT 587,
    smtp_user TEXT DEFAULT '',
    smtp_pass TEXT DEFAULT '',
    smtp_tls BOOLEAN DEFAULT true,
    from_email TEXT DEFAULT '',
    from_name TEXT DEFAULT 'WhatsApp Monitor',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Scheduled Reports ──
CREATE TABLE IF NOT EXISTS wa_scheduled_reports (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'daily_summary',
    schedule TEXT NOT NULL DEFAULT 'daily',
    schedule_time TEXT DEFAULT '08:00',
    schedule_day INTEGER DEFAULT 1,
    recipients_email TEXT NOT NULL DEFAULT '',
    group_filter TEXT DEFAULT '',
    include_stats BOOLEAN DEFAULT true,
    is_active BOOLEAN DEFAULT true,
    last_sent_at TIMESTAMPTZ,
    last_status TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Webhook Debug Log ──
CREATE TABLE IF NOT EXISTS wa_webhook_log (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    instance TEXT,
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wa_webhook_unprocessed
    ON wa_webhook_log (processed) WHERE NOT processed;
