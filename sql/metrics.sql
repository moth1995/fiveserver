-- Metrics schema additions (step 15).
-- Apply after schema.sql. Does NOT modify existing tables.

CREATE TABLE IF NOT EXISTS match_rosters (
    match_id         BIGINT UNSIGNED NOT NULL,
    home_roster_hash CHAR(32)        NULL,
    away_roster_hash CHAR(32)        NULL,
    started_at       TIMESTAMP       NULL,
    PRIMARY KEY (match_id),
    FOREIGN KEY (match_id) REFERENCES matches (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
