CREATE TABLE IF NOT EXISTS solar_app_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'customer', 'viewer')),
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS solar_app_user_plants (
    user_id BIGINT NOT NULL REFERENCES solar_app_users(id) ON DELETE CASCADE,
    plant_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, plant_key)
);

CREATE INDEX IF NOT EXISTS idx_solar_app_user_plants_key
    ON solar_app_user_plants (plant_key);
