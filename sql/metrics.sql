-- Metrics schema additions.
-- Apply after schema.sql. Does NOT modify existing tables.

CREATE TABLE IF NOT EXISTS match_rosters (
    match_id         BIGINT UNSIGNED NOT NULL,
    home_roster_hash CHAR(32)        NULL,
    away_roster_hash CHAR(32)        NULL,
    started_at       TIMESTAMP       NULL,
    PRIMARY KEY (match_id),
    FOREIGN KEY (match_id) REFERENCES matches (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Tracks when each user account was created.
-- Populated by the Flask registration endpoint on every new sign-up.
-- Kept separate from users so the core schema is not modified.
CREATE TABLE IF NOT EXISTS user_registrations (
    user_id       INT UNSIGNED NOT NULL,
    registered_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
