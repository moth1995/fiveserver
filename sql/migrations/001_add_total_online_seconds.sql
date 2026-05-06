-- Migration 001: add total_online_seconds to users table
-- Safe to run multiple times (idempotent via information_schema check).
-- Compatible with MariaDB 10+ and MySQL 8.0+.
--
-- For existing Docker deployments (existing db_data volume), run:
--   docker compose exec db sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
--     < sql/migrations/001_add_total_online_seconds.sql

SET @q = (SELECT IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE()
     AND TABLE_NAME   = 'users'
     AND COLUMN_NAME  = 'total_online_seconds') > 0,
  'SELECT 1 -- column already exists',
  'ALTER TABLE users ADD COLUMN total_online_seconds BIGINT UNSIGNED NOT NULL DEFAULT 0'
));
PREPARE _mig FROM @q;
EXECUTE _mig;
DEALLOCATE PREPARE _mig;
