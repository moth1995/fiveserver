package config_test

import (
	"os"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
)

const baseYAML = `
ServerName: "Before"
MaxUsers: 100
Debug: false
Log:
  file: ""
  level: info
Lobbies:
  - Russia
  - England
`

const updatedYAML = `
ServerName: "After"
MaxUsers: 200
Debug: true
Log:
  file: ""
  level: debug
Lobbies:
  - Russia
  - England
  - Italy
`

func TestReload_ChangesDetected(t *testing.T) {
	f1 := writeTemp(t, baseYAML)
	cfg, err := config.Load(f1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	f2 := writeTemp(t, updatedYAML)
	changes, err := cfg.Reload(f2)
	if err != nil {
		t.Fatalf("Reload: %v", err)
	}
	if len(changes) == 0 {
		t.Fatal("expected changes, got none")
	}

	if cfg.ServerName != "After" {
		t.Errorf("ServerName want After, got %q", cfg.ServerName)
	}
	if cfg.MaxUsers != 200 {
		t.Errorf("MaxUsers want 200, got %d", cfg.MaxUsers)
	}
	if !cfg.Debug {
		t.Error("Debug want true")
	}
	if len(cfg.Lobbies) != 3 {
		t.Errorf("Lobbies want 3, got %d", len(cfg.Lobbies))
	}
}

func TestReload_InvalidYAML_ReturnsError(t *testing.T) {
	f1 := writeTemp(t, baseYAML)
	cfg, err := config.Load(f1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	bad := writeTemp(t, ":\tinvalid: yaml: [[[")
	_, err = cfg.Reload(bad)
	if err == nil {
		t.Fatal("expected error for invalid YAML, got nil")
	}
}

func TestReload_NonReloadableFieldsIgnored(t *testing.T) {
	f1 := writeTemp(t, baseYAML+`
NetworkServer:
  mainService: 20100
  networkMenuService: 20101
  loginService:
    pes5: 20102
`)
	cfg, err := config.Load(f1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	origPort := cfg.NetworkServer.MainService

	f2 := writeTemp(t, baseYAML+`
NetworkServer:
  mainService: 99999
  networkMenuService: 99998
  loginService:
    pes5: 99997
`)
	if _, err := cfg.Reload(f2); err != nil {
		t.Fatalf("Reload: %v", err)
	}
	if cfg.NetworkServer.MainService != origPort {
		t.Errorf("MainService should not reload: want %d, got %d", origPort, cfg.NetworkServer.MainService)
	}
}

func TestReload_MissingFile_ReturnsError(t *testing.T) {
	f1 := writeTemp(t, baseYAML)
	cfg, err := config.Load(f1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	_, err = cfg.Reload("/nonexistent/path/fiveserver.yaml")
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
}

func TestReload_IsBannedAfterReload(t *testing.T) {
	f1 := writeTemp(t, baseYAML)
	cfg, err := config.Load(f1)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	// Write a banned list
	bannedFile, _ := os.CreateTemp(t.TempDir(), "banned*.yaml")
	bannedFile.WriteString("Banned:\n  - 10.0.0.1\n")
	bannedFile.Close()

	// Update config to point to banned list and reload
	f2 := writeTemp(t, baseYAML+"BannedList: "+bannedFile.Name()+"\n")
	if _, err := cfg.Reload(f2); err != nil {
		t.Fatalf("Reload: %v", err)
	}

	if !cfg.IsBanned("10.0.0.1") {
		t.Error("expected 10.0.0.1 to be banned after reload")
	}
	if cfg.IsBanned("10.0.0.2") {
		t.Error("expected 10.0.0.2 to not be banned")
	}
}
