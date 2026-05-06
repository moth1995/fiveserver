package main

import (
	"context"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/admin"
	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/logger"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
	"github.com/fiveserver/fiveserver-go/internal/server"
)

func main() {
	configPath := flag.String("config", "./etc/conf/fiveserver.yaml", "path to fiveserver.yaml")
	adminConfigPath := flag.String("admin-config", "./etc/conf/admin.yaml", "path to admin.yaml")
	flag.Parse()

	// ---- 0. Pre-config logger (stdout, info) -----------------------------------
	if err := logger.Init("info", ""); err != nil {
		panic(err)
	}

	// ---- 1. Load config --------------------------------------------------------
	cfg, err := config.Load(*configPath)
	if err != nil {
		logger.Criticalf("config: %v", err)
		os.Exit(1)
	}
	logger.Infof("fiveserver: loaded config from %s", *configPath)

	adminCfg, err := config.LoadAdmin(*adminConfigPath)
	if err != nil {
		logger.Criticalf("config: %v", err)
		os.Exit(1)
	}
	if adminCfg.AdminPort != 0 {
		logger.Infof("fiveserver: loaded admin config from %s (AdminPort=%d)", *adminConfigPath, adminCfg.AdminPort)
	} else {
		logger.Warnf("fiveserver: admin config not found or AdminPort not set (%s) — admin API disabled", *adminConfigPath)
	}

	// ---- 1b. Re-init logger with configured file and level ---------------------
	// FiveserverLogFile from admin.yaml takes precedence over Log.File from fiveserver.yaml.
	logFile := adminCfg.FiveserverLogFile
	logLevel := "info"
	if cfg.Debug {
		logLevel = "debug"
	}
	if err := logger.Init(logLevel, logFile); err != nil {
		logger.Warnf("fiveserver: could not open log file %q: %v — continuing with stdout only", logFile, err)
	}

	cfg.ResolveServerIP()

	// ---- 2. Init storage controller -------------------------------------------
	sc, err := db.NewStorageController(cfg.DB)
	if err != nil {
		logger.Criticalf("db: %v", err)
		os.Exit(1)
	}
	defer func() {
		if err := sc.Close(); err != nil {
			logger.Errorf("db: close: %v", err)
		}
	}()

	// ---- 3. Keep-alive goroutine -----------------------------------------------
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	keepAliveInterval := time.Duration(cfg.DB.ConnectionPool.KeepAliveInterval) * time.Second
	if keepAliveInterval <= 0 {
		keepAliveInterval = 60 * time.Second
	}
	sc.StartKeepAlive(ctx, keepAliveInterval)

	// ---- 4. Shared Hub ---------------------------------------------------------
	hub := protocol.NewHub(cfg)
	hub.OnUserConfirmedOffline = func(userID int, seconds int64) {
		if err := db.AddUserOnlineSeconds(context.Background(), sc, userID, seconds); err != nil {
			logger.Errorf("[hub] AddUserOnlineSeconds user=%d: %v", userID, err)
		}
	}
	protocol.StartDayChangeTimer(hub)
	startRanksTimer(sc, cfg)

	// ---- 5. Listeners ----------------------------------------------------------
	var wg sync.WaitGroup
	listenOn := cfg.ListenOn
	if listenOn == "" {
		listenOn = "0.0.0.0"
	}

	// Admin HTTP server — port comes from admin.yaml AdminPort
	if adminCfg.AdminPort != 0 {
		adminAddr := fmt.Sprintf("%s:%d", listenOn, adminCfg.AdminPort)
		go func() {
			adminSrv := admin.NewServer(hub, cfg, adminCfg, *configPath)
			if err := adminSrv.ListenAndServe(adminAddr); err != nil {
				logger.Errorf("admin: %v", err)
			}
		}()
	}

	usedPorts := make(map[int]bool)
	listen := func(addr string, port int, d *protocol.Dispatcher, filter ...func(string) error) {
		if usedPorts[port] {
			logger.Warnf("skipping duplicate port %d", port)
			return
		}
		usedPorts[port] = true
		wg.Add(1)
		go func() {
			defer wg.Done()
			logger.Infof("listening on %s", addr)
			if err := server.Serve(addr, d, ctx.Done(), func() bool { return cfg.Debug }, filter...); err != nil {
				logger.Errorf("server %s: %v", addr, err)
			}
		}()
	}

	// connectFilter rejects banned IPs and connections when the server is at
	// capacity. Mirrors Python IsBanned + AtCapacity guards in LoginService.
	connectFilter := func(remoteAddr string) error {
		host := remoteAddr
		if h, _, err := net.SplitHostPort(remoteAddr); err == nil {
			host = h
		}
		if cfg.IsBanned(host) {
			return fmt.Errorf("banned")
		}
		if hub.AtCapacity() {
			return fmt.Errorf("server at capacity")
		}
		return nil
	}

	// News service — one per game version
	for version, port := range cfg.GamePorts {
		v, p := version, port
		listen(fmt.Sprintf("%s:%d", listenOn, p), p,
			protocol.NewNewsDispatcher(hub, v))
	}

	// Login service — one per game version
	for version, port := range cfg.NetworkServer.LoginService {
		v, p := version, port
		listen(fmt.Sprintf("%s:%d", listenOn, p), p,
			protocol.NewLoginDispatcher(hub, sc, v), connectFilter)
	}

	// Network menu service
	if cfg.NetworkServer.NetworkMenuService != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.NetworkServer.NetworkMenuService), cfg.NetworkServer.NetworkMenuService,
			protocol.NewMenuDispatcher(hub, sc, "pes5"), connectFilter)
	}

	// Main game service
	if cfg.NetworkServer.MainService != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.NetworkServer.MainService), cfg.NetworkServer.MainService,
			protocol.NewMainServiceDispatcher(hub, sc, "pes5"), connectFilter)
	}

	// ---- 7. Wait for shutdown signal -------------------------------------------

	logger.Infof("fiveserver: all listeners started — Ctrl+C to stop")
	<-ctx.Done()
	logger.Infof("fiveserver: shutting down…")
	wg.Wait()
	logger.Infof("fiveserver: stopped")
}

// startRanksTimer schedules periodic rank recomputation based on
// ComputeRanksInterval from fiveserver.yaml (mirrors Python computeRanks timer).
// Interval = days*86400 + seconds. Falls back to 24 h if both are zero.
func startRanksTimer(sc *db.StorageController, cfg *config.Config) {
	var tick func()
	tick = func() {
		ri := cfg.ComputeRanksInterval
		interval := time.Duration(ri.Days)*24*time.Hour + time.Duration(ri.Seconds)*time.Second
		if interval <= 0 {
			interval = 24 * time.Hour
		}
		logger.Infof("[ranks] recomputing ranks (interval=%s)", interval)
		if err := db.ComputeRanks(context.Background(), sc); err != nil {
			logger.Errorf("[ranks] ComputeRanks failed: %v", err)
		}
		time.AfterFunc(interval, tick)
	}
	ri := cfg.ComputeRanksInterval
	initial := time.Duration(ri.Days)*24*time.Hour + time.Duration(ri.Seconds)*time.Second
	if initial <= 0 {
		initial = 24 * time.Hour
	}
	time.AfterFunc(initial, tick)
}
