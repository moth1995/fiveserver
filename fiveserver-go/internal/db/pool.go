package db

import (
	"context"
	"database/sql"
	"fmt"
	"sync"
	"time"

	_ "github.com/go-sql-driver/mysql"
	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/logger"
)

// Pool holds one or more *sql.DB handles and picks among them round-robin.
type Pool struct {
	dbs []*sql.DB
	mu  sync.Mutex
	idx int
}

// NewPool opens a *sql.DB for each DSN. Returns an error if any open fails.
func NewPool(dsns []string, cfg config.ConnectionPoolConfig) (*Pool, error) {
	if len(dsns) == 0 {
		return nil, fmt.Errorf("db: pool requires at least one DSN")
	}
	p := &Pool{}
	for _, dsn := range dsns {
		db, err := sql.Open("mysql", dsn)
		if err != nil {
			p.Close()
			return nil, fmt.Errorf("db: open %q: %w", dsn, err)
		}
		db.SetMaxOpenConns(cfg.MaxConnections)
		db.SetMaxIdleConns(cfg.MinConnections)
		db.SetConnMaxLifetime(time.Hour)
		p.dbs = append(p.dbs, db)
	}
	return p, nil
}

// DB returns the next *sql.DB in round-robin order. Thread-safe.
func (p *Pool) DB() *sql.DB {
	p.mu.Lock()
	defer p.mu.Unlock()
	db := p.dbs[p.idx%len(p.dbs)]
	p.idx++
	return db
}

// Close closes all *sql.DB handles; returns the first error encountered.
func (p *Pool) Close() error {
	var first error
	for _, db := range p.dbs {
		if err := db.Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
}

// ping sends a lightweight ping to every db handle in the pool.
func (p *Pool) ping(ctx context.Context) {
	p.mu.Lock()
	dbs := make([]*sql.DB, len(p.dbs))
	copy(dbs, p.dbs)
	p.mu.Unlock()
	for _, db := range dbs {
		_ = db.PingContext(ctx)
	}
}

// StorageController holds a read pool and a write pool.
// When SharePool is true both fields point to the same Pool.
type StorageController struct {
	Read  *Pool
	Write *Pool
}

// NewStorageController builds DSNs from cfg and opens the pools.
func NewStorageController(cfg config.DBConfig) (*StorageController, error) {
	logger.Infof("db: connecting as user=%q db=%q", cfg.User, cfg.Name)
	buildDSNs := func(hosts []string) []string {
		dsns := make([]string, len(hosts))
		for i, h := range hosts {
			dsns[i] = fmt.Sprintf("%s:%s@tcp(%s:3306)/%s?charset=utf8mb4&parseTime=true",
				cfg.User, cfg.Password, h, cfg.Name)
		}
		return dsns
	}

	writePool, err := NewPool(buildDSNs(cfg.WriteServers), cfg.ConnectionPool)
	if err != nil {
		return nil, fmt.Errorf("db: write pool: %w", err)
	}

	if cfg.SharePool {
		return &StorageController{Read: writePool, Write: writePool}, nil
	}

	readPool, err := NewPool(buildDSNs(cfg.ReadServers), cfg.ConnectionPool)
	if err != nil {
		writePool.Close()
		return nil, fmt.Errorf("db: read pool: %w", err)
	}
	return &StorageController{Read: readPool, Write: writePool}, nil
}

// Close closes both pools (read pool skipped when it is the same as write pool).
func (sc *StorageController) Close() error {
	err := sc.Write.Close()
	if sc.Read != sc.Write {
		if e := sc.Read.Close(); e != nil && err == nil {
			err = e
		}
	}
	return err
}

// StartKeepAlive pings all pools on every interval until ctx is cancelled.
// Mirrors Python KeepAliveManager.start().
func (sc *StorageController) StartKeepAlive(ctx context.Context, interval time.Duration) {
	go func() {
		t := time.NewTicker(interval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				sc.Write.ping(ctx)
				if sc.Read != sc.Write {
					sc.Read.ping(ctx)
				}
			}
		}
	}()
}
