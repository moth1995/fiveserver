package db_test

import (
	"context"
	"testing"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/db"
)

var testPoolCfg = config.ConnectionPoolConfig{
	MinConnections:    1,
	MaxConnections:    3,
	KeepAliveInterval: 60,
}

// validDSN uses a fake host; sql.Open succeeds without a real server.
func validDSN(host string) string {
	return "fiveserver:we9le@tcp(" + host + ":3306)/fiveserver?charset=utf8mb4&parseTime=true"
}

// testDBConfig returns a DBConfig pointing at a non-reachable host for unit tests.
func testDBConfig() config.DBConfig {
	return config.DBConfig{
		Name:           "fiveserver",
		User:           "fiveserver",
		Password:       "we9le",
		ReadServers:    []string{"127.0.0.1"},
		WriteServers:   []string{"127.0.0.1"},
		SharePool:      true,
		ConnectionPool: testPoolCfg,
	}
}

func TestNewPool_SingleServer(t *testing.T) {
	p, err := db.NewPool([]string{validDSN("127.0.0.1")}, testPoolCfg)
	if err != nil {
		t.Fatalf("NewPool: %v", err)
	}
	defer p.Close()
	if p.DB() == nil {
		t.Fatal("DB() returned nil")
	}
}

func TestNewPool_Empty(t *testing.T) {
	_, err := db.NewPool([]string{}, testPoolCfg)
	if err == nil {
		t.Fatal("expected error for empty DSN list")
	}
}

func TestPool_RoundRobin(t *testing.T) {
	p, err := db.NewPool([]string{
		validDSN("10.0.0.1"),
		validDSN("10.0.0.2"),
	}, testPoolCfg)
	if err != nil {
		t.Fatalf("NewPool: %v", err)
	}
	defer p.Close()

	// Call DB() 4 times — should cycle through both handles
	seen := map[interface{}]int{}
	for i := 0; i < 4; i++ {
		seen[p.DB()]++
	}
	if len(seen) != 2 {
		t.Errorf("expected 2 distinct handles in 4 calls, got %d", len(seen))
	}
}

func TestNewStorageController_SharePool(t *testing.T) {
	cfg := config.DBConfig{
		Name:           "fiveserver",
		User:           "fiveserver",
		Password:       "we9le",
		ReadServers:    []string{"127.0.0.1"},
		WriteServers:   []string{"127.0.0.1"},
		SharePool:      true,
		ConnectionPool: testPoolCfg,
	}
	sc, err := db.NewStorageController(cfg)
	if err != nil {
		t.Fatalf("NewStorageController: %v", err)
	}
	defer sc.Close()
	if sc.Read != sc.Write {
		t.Error("SharePool=true: Read and Write pools should be the same pointer")
	}
}

func TestNewStorageController_SeparatePools(t *testing.T) {
	cfg := config.DBConfig{
		Name:           "fiveserver",
		User:           "fiveserver",
		Password:       "we9le",
		ReadServers:    []string{"10.0.0.1"},
		WriteServers:   []string{"10.0.0.2"},
		SharePool:      false,
		ConnectionPool: testPoolCfg,
	}
	sc, err := db.NewStorageController(cfg)
	if err != nil {
		t.Fatalf("NewStorageController: %v", err)
	}
	defer sc.Close()
	if sc.Read == sc.Write {
		t.Error("SharePool=false: Read and Write pools should be different pointers")
	}
}

func TestStartKeepAlive_StopsOnCancel(t *testing.T) {
	cfg := config.DBConfig{
		Name:           "fiveserver",
		User:           "fiveserver",
		Password:       "we9le",
		ReadServers:    []string{"127.0.0.1"},
		WriteServers:   []string{"127.0.0.1"},
		SharePool:      true,
		ConnectionPool: testPoolCfg,
	}
	sc, err := db.NewStorageController(cfg)
	if err != nil {
		t.Fatalf("NewStorageController: %v", err)
	}
	defer sc.Close()

	ctx, cancel := context.WithCancel(context.Background())
	sc.StartKeepAlive(ctx, 10*time.Millisecond)
	time.Sleep(25 * time.Millisecond) // let it tick at least twice
	cancel()
	time.Sleep(15 * time.Millisecond) // goroutine should exit
	// no assertion needed — test passes if it doesn't deadlock/panic
}
