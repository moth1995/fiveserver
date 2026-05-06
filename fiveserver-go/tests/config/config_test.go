package config_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
)

// writeTemp writes content to a temp file and returns its path.
func writeTemp(t *testing.T, content string) string {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "*.yaml")
	if err != nil {
		t.Fatalf("temp file: %v", err)
	}
	if _, err := f.WriteString(content); err != nil {
		t.Fatalf("write temp: %v", err)
	}
	_ = f.Close()
	return f.Name()
}

func TestLoad_RealFiveserverYaml(t *testing.T) {
	path := filepath.Join("testdata", "fiveserver.yaml")
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.ServerName == "" {
		t.Error("ServerName should not be empty")
	}
	if len(cfg.Lobbies) == 0 {
		t.Error("expected at least one lobby")
	}
	if cfg.DB.Name == "" {
		t.Error("DB.Name should not be empty")
	}
	if cfg.MaxUsers == 0 {
		t.Error("MaxUsers should default to 1000")
	}
}

func TestLoad_LobbyStringForm(t *testing.T) {
	yaml := writeTemp(t, `
ServerName: test
Lobbies:
  - Russia
  - England
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(yaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(cfg.Lobbies) != 2 {
		t.Fatalf("expected 2 lobbies, got %d", len(cfg.Lobbies))
	}
	if cfg.Lobbies[0].Name != "Russia" {
		t.Errorf("lobby[0].Name = %q, want Russia", cfg.Lobbies[0].Name)
	}
	// default open type code
	if cfg.Lobbies[0].TypeCode != 0x5f {
		t.Errorf("lobby[0].TypeCode = 0x%x, want 0x5f", cfg.Lobbies[0].TypeCode)
	}
}

func TestLoad_LobbyStructuredForm(t *testing.T) {
	yaml := writeTemp(t, `
Lobbies:
  - name: Playground
    type: [A]
  - name: Training
    type: noStats
    showMatches: false
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(yaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Lobbies[0].Name != "Playground" {
		t.Errorf("lobby[0].Name = %q", cfg.Lobbies[0].Name)
	}
	if cfg.Lobbies[0].TypeCode != 0x01 { // division A = bit 0
		t.Errorf("lobby[0].TypeCode = 0x%x, want 0x01", cfg.Lobbies[0].TypeCode)
	}
	if cfg.Lobbies[1].TypeCode != 0x20 { // noStats
		t.Errorf("lobby[1].TypeCode = 0x%x, want 0x20", cfg.Lobbies[1].TypeCode)
	}
	if cfg.Lobbies[1].ShowMatches {
		t.Error("lobby[1].ShowMatches should be false")
	}
}

func TestLoad_LobbyStructuredForm_InvalidDivisionFails(t *testing.T) {
	yaml := writeTemp(t, `
Lobbies:
  - name: Broken
    type: [A, Z]
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	_, err := config.Load(yaml)
	if err == nil {
		t.Fatal("Load should fail for unrecognized lobby division")
	}
}

func TestLoad_LobbyStructuredForm_UnknownStringTypeDefaultsToOpen(t *testing.T) {
	yaml := writeTemp(t, `
Lobbies:
  - name: Weird
    type: restricted
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(yaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Lobbies[0].TypeCode != 0x5f {
		t.Errorf("lobby[0].TypeCode = 0x%x, want 0x5f", cfg.Lobbies[0].TypeCode)
	}
}

func TestIsBanned_ExactIP(t *testing.T) {
	bannedYaml := writeTemp(t, "Banned:\n  - 1.2.3.4\n")
	cfgYaml := writeTemp(t, `
BannedList: `+bannedYaml+`
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(cfgYaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.IsBanned("1.2.3.4") {
		t.Error("1.2.3.4 should be banned")
	}
	if cfg.IsBanned("1.2.3.5") {
		t.Error("1.2.3.5 should not be banned")
	}
}

func TestIsBanned_CIDRRange(t *testing.T) {
	bannedYaml := writeTemp(t, "Banned:\n  - 10.0.0.0/8\n")
	cfgYaml := writeTemp(t, `
BannedList: `+bannedYaml+`
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(cfgYaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.IsBanned("10.1.2.3") {
		t.Error("10.1.2.3 should be banned (10.0.0.0/8)")
	}
	if cfg.IsBanned("11.0.0.1") {
		t.Error("11.0.0.1 should not be banned")
	}
}

func TestIsBanned_NoBannedList(t *testing.T) {
	cfgYaml := writeTemp(t, `
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(cfgYaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.IsBanned("1.2.3.4") {
		t.Error("no banned list — nothing should be banned")
	}
}

func TestLoad_MaxUsersDefault(t *testing.T) {
	cfgYaml := writeTemp(t, `
DB:
  name: db
  user: u
  password: p
  readServers: [127.0.0.1]
  writeServers: [127.0.0.1]
`)
	cfg, err := config.Load(cfgYaml)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.MaxUsers != 1000 {
		t.Errorf("MaxUsers default = %d, want 1000", cfg.MaxUsers)
	}
}
