package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/admin"
	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
	"github.com/fiveserver/fiveserver-go/internal/server"
)

func main() {
	configPath := flag.String("config", "etc/conf/fiveserver.yaml", "path to fiveserver.yaml")
	flag.Parse()

	// ---- 1. Load config --------------------------------------------------------
	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	log.Printf("fiveserver: loaded config from %s", *configPath)
	cfg.ResolveServerIP()

	// ---- 2. Init storage controller -------------------------------------------
	sc, err := db.NewStorageController(cfg.DB)
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	defer func() {
		if err := sc.Close(); err != nil {
			log.Printf("db: close: %v", err)
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
	if cfg.WebInterface.Port != 0 {
		adminAddr := fmt.Sprintf("%s:%d", listenOn, cfg.WebInterface.Port)
		go func() {
			adminSrv := admin.NewServer(hub)
			if err := adminSrv.ListenAndServe(adminAddr); err != nil {
				log.Printf("admin: %v", err)
			}
		}()
	}

	usedPorts := make(map[int]bool)
	listen := func(addr string, port int, d *protocol.Dispatcher, filter ...func(string) error) {
		if usedPorts[port] {
			log.Printf("skipping duplicate port %d", port)
			return
		}
		usedPorts[port] = true
		wg.Add(1)
		go func() {
			defer wg.Done()
			log.Printf("listening on %s", addr)
			if err := server.Serve(addr, d, ctx.Done(), cfg.Debug, filter...); err != nil {
				log.Printf("server %s: %v", addr, err)
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
	for _, gp := range cfg.GamePorts {
		gp := gp
		listen(fmt.Sprintf("%s:%d", listenOn, gp.Port), gp.Port,
			protocol.NewNewsDispatcher(hub, gp.Version))
	}

	// Login service — one per game version
	for _, ls := range cfg.NetworkServer.LoginService {
		ls := ls
		listen(fmt.Sprintf("%s:%d", listenOn, ls.Port), ls.Port,
			protocol.NewLoginDispatcher(hub, sc, ls.Version), connectFilter)
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
	log.Printf("fiveserver: all listeners started — Ctrl+C to stop")
	<-ctx.Done()
	log.Printf("fiveserver: shutting down…")
	wg.Wait()
	log.Printf("fiveserver: stopped")
}
