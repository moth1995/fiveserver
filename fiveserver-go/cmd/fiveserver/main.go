package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

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

	// ---- 5. Listeners ----------------------------------------------------------
	var wg sync.WaitGroup
	listenOn := cfg.ListenOn
	if listenOn == "" {
		listenOn = "0.0.0.0"
	}

	listen := func(addr string, d *protocol.Dispatcher) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			log.Printf("listening on %s", addr)
			if err := server.Serve(addr, d, ctx.Done()); err != nil {
				log.Printf("server %s: %v", addr, err)
			}
		}()
	}

	// News service — one per game version
	if cfg.GamePorts.PES5 != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.GamePorts.PES5),
			protocol.NewNewsDispatcher(hub, "pes5"))
	}
	if cfg.GamePorts.WE9 != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.GamePorts.WE9),
			protocol.NewNewsDispatcher(hub, "we9"))
	}
	if cfg.GamePorts.WE9LE != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.GamePorts.WE9LE),
			protocol.NewNewsDispatcher(hub, "we9le"))
	}

	// Login service — one per game version
	for version, port := range cfg.NetworkServer.LoginService {
		v, p := version, port
		listen(fmt.Sprintf("%s:%d", listenOn, p),
			protocol.NewLoginDispatcher(hub, sc, v))
	}

	// Network menu service
	if cfg.NetworkServer.NetworkMenuService != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.NetworkServer.NetworkMenuService),
			protocol.NewMenuDispatcher(hub, sc, "pes5"))
	}

	// Main game service
	if cfg.NetworkServer.MainService != 0 {
		listen(fmt.Sprintf("%s:%d", listenOn, cfg.NetworkServer.MainService),
			protocol.NewMainServiceDispatcher(hub, sc, "pes5"))
	}

	// ---- 6. Wait for shutdown signal -------------------------------------------
	log.Printf("fiveserver: all listeners started — Ctrl+C to stop")
	<-ctx.Done()
	log.Printf("fiveserver: shutting down…")
	wg.Wait()
	log.Printf("fiveserver: stopped")
}
