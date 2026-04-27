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
	configPath := flag.String("config", "etc/conf/fiveserver.yaml", "path to fiveserver.yaml")
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

	// ---- 1b. Re-init logger with configured file and level ---------------------
	if err := logger.Init(cfg.Log.Level, cfg.Log.File); err != nil {
		logger.Warnf("fiveserver: could not open log file %q: %v — continuing with stdout only", cfg.Log.File, err)
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
	protocol.StartDayChangeTimer(hub)

	// ---- 5. Listeners ----------------------------------------------------------
	var wg sync.WaitGroup
	listenOn := cfg.ListenOn
	if listenOn == "" {
		listenOn = "0.0.0.0"
	}

	// Admin HTTP server
	if cfg.WebInterface.AdminPort != 0 {
		adminAddr := fmt.Sprintf("%s:%d", listenOn, cfg.WebInterface.AdminPort)
		go func() {
			adminSrv := admin.NewServer(hub, cfg, *configPath)
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
			if err := server.Serve(addr, d, ctx.Done(), cfg.Debug, filter...); err != nil {
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
