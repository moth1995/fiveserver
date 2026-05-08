-- Migration 001: update views to include seconds_played in v_leaderboard
--                and add v_match_detail (new)
-- Safe to re-run: all statements use CREATE OR REPLACE VIEW
--
-- Apply:
--   mysql fiveserver < sql/migrations/001_update_views.sql
-- OR equivalently:
--   mysql fiveserver < sql/views.sql

CREATE OR REPLACE VIEW v_profile_stats AS
SELECT
    p.id,
    COALESCE(st.games,         0) AS games,
    COALESCE(st.wins,          0) AS wins,
    COALESCE(st.draws,         0) AS draws,
    COALESCE(st.losses,        0) AS losses,
    COALESCE(st.goals_for,     0) AS goals_for,
    COALESCE(st.goals_against, 0) AS goals_against
FROM profiles p
LEFT JOIN (
    SELECT
        x.pid,
        COUNT(*)                                                 AS games,
        SUM(CASE WHEN x.s_for > x.s_against THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN x.s_for = x.s_against THEN 1 ELSE 0 END) AS draws,
        SUM(CASE WHEN x.s_for < x.s_against THEN 1 ELSE 0 END) AS losses,
        SUM(x.s_for)                                            AS goals_for,
        SUM(x.s_against)                                        AS goals_against
    FROM (
        SELECT profile_id_home AS pid, score_home AS s_for,  score_away AS s_against FROM matches
        UNION ALL
        SELECT profile_id_away,        score_away,            score_home             FROM matches
    ) x
    GROUP BY x.pid
) st ON st.pid = p.id;

CREATE OR REPLACE VIEW v_leaderboard AS
SELECT
    p.rank,
    p.id,
    p.name,
    p.points,
    p.seconds_played,
    COALESCE(ps.games,  0) AS games,
    COALESCE(ps.wins,   0) AS wins,
    COALESCE(ps.draws,  0) AS draws,
    COALESCE(ps.losses, 0) AS losses,
    COALESCE(s.best,    0) AS best,
    CASE
        WHEN p.points < 250 THEN 0
        WHEN p.points < 450 THEN 1
        WHEN p.points < 600 THEN 2
        WHEN p.points < 750 THEN 3
        ELSE 4
    END AS division
FROM profiles p
LEFT JOIN v_profile_stats ps ON ps.id        = p.id
LEFT JOIN streaks          s  ON s.profile_id = p.id
WHERE p.deleted = 0;

CREATE OR REPLACE VIEW v_match_detail AS
SELECT
    m.id,
    m.profile_id_home, ph.name AS home_name,
    m.profile_id_away, pa.name AS away_name,
    m.score_home,
    m.score_away,
    m.team_id_home,
    m.team_id_away,
    m.played_on
FROM matches m
LEFT JOIN profiles ph ON ph.id = m.profile_id_home
LEFT JOIN profiles pa ON pa.id = m.profile_id_away;
