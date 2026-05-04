package config

import (
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/logger"
	"gopkg.in/yaml.v3"
)

// ---- top-level config -------------------------------------------------------

type LogConfig struct {
	File  string `yaml:"file"`
	Level string `yaml:"level"`
}

type Config struct {
	ServerIP             string              `yaml:"ServerIP"`
	ListenOn             string              `yaml:"ListenOn"`
	IpDetectUri          string              `yaml:"IpDetectUri"`
	Lobbies              []Lobby             `yaml:"Lobbies"`
	GamePorts            map[string]int      `yaml:"GamePorts"`
	NetworkServer        NetworkServerConfig `yaml:"NetworkServer"`
	WebInterface         WebInterfaceConfig  `yaml:"WebInterface"`
	Debug                bool                `yaml:"Debug"`
	Log                  LogConfig           `yaml:"Log"`
	DB                   DBConfig            `yaml:"DB"`
	BannedList           string              `yaml:"BannedList"`
	Chat                 ChatConfig          `yaml:"Chat"`
	Roster               RosterConfig        `yaml:"Roster"`
	ComputeRanksInterval RanksInterval       `yaml:"ComputeRanksInterval"`
	StoreSettings        bool                `yaml:"StoreSettings"`
	ShowStats            bool                `yaml:"ShowStats"`
	Disconnects          DisconnectsConfig   `yaml:"Disconnects"`
	ServerName           string              `yaml:"ServerName"`
	Greeting             GreetingConfig      `yaml:"Greeting"`
	MaxUsers             int                 `yaml:"MaxUsers"`
	// NewFeatures maps game-version strings ("pes5", "we9", "we9le") to an
	// additional 0x200a announcement sent after the greeting.
	// Mirrors Python NEW_FEATURES dict in protocol/pes5.py.
	NewFeatures map[string]NewFeatureEntry `yaml:"NewFeatures"`

	// runtime-only: parsed banned list entries (net, mask) in host byte order
	fastBanned []bannedEntry
	// mu protects fastBanned and all live-reloadable config fields during Reload.
	mu sync.RWMutex
	// ipMu protects resolvedIP only (separate to avoid blocking IP resolution).
	ipMu       sync.RWMutex
	resolvedIP string
}

// ---- nested types -----------------------------------------------------------

// Lobby can be either a plain string ("Russia") or a structured map.
// UnmarshalYAML handles both forms.
type Lobby struct {
	Name            string
	Type            string   // "open", "noStats", or comma-joined division list
	TypeList        []string // set when type is a YAML list (e.g. ["A"])
	ShowMatches     bool
	CheckRosterHash bool
	TypeCode        byte
}

func (l *Lobby) UnmarshalYAML(value *yaml.Node) error {
	// Defaults
	l.ShowMatches = true
	l.CheckRosterHash = true
	l.Type = "open"

	switch value.Kind {
	case yaml.ScalarNode:
		// plain string lobby name
		l.Name = value.Value
	case yaml.MappingNode:
		// structured lobby definition
		var raw struct {
			Name            string      `yaml:"name"`
			Type            interface{} `yaml:"type"`
			ShowMatches     *bool       `yaml:"showMatches"`
			CheckRosterHash *bool       `yaml:"checkRosterHash"`
		}
		if err := value.Decode(&raw); err != nil {
			return err
		}
		l.Name = raw.Name
		if raw.ShowMatches != nil {
			l.ShowMatches = *raw.ShowMatches
		}
		if raw.CheckRosterHash != nil {
			l.CheckRosterHash = *raw.CheckRosterHash
		}
		switch t := raw.Type.(type) {
		case string:
			l.Type = t
		case []interface{}:
			for _, v := range t {
				l.TypeList = append(l.TypeList, fmt.Sprintf("%v", v))
			}
		}
	default:
		return fmt.Errorf("config: unexpected lobby YAML node kind %v", value.Kind)
	}
	typeCode, err := lobbyTypeCode(l.Type, l.TypeList)
	if err != nil {
		return err
	}
	l.TypeCode = typeCode
	return nil
}

// lobbyTypeCode matches Python FiveServerConfig.__init__ lobby-type logic.
func lobbyTypeCode(typ string, list []string) (byte, error) {
	if len(list) > 0 {
		divMap := map[string]int{"A": 0, "3B": 1, "3A": 2, "2": 3, "1": 4}
		code := 0
		for _, d := range list {
			v, ok := divMap[d]
			if !ok {
				return 0, fmt.Errorf("config: invalid lobby type definition: unrecognized division %q", d)
			}
			code += 1 << v
		}
		return byte(code), nil
	}

	switch typ {
	case "noStats":
		return 0x20, nil
	case "open":
		return 0x5f, nil
	default: // "open" or anything else
		return 0x5f, nil
	}
}

type NetworkServerConfig struct {
	MainService        int            `yaml:"mainService"`
	NetworkMenuService int            `yaml:"networkMenuService"`
	LoginService       map[string]int `yaml:"loginService"`
}

type WebInterfaceConfig struct {
	Port      int `yaml:"port"`
	AdminPort int `yaml:"adminPort"`
}

type ConnectionPoolConfig struct {
	MinConnections    int `yaml:"minConnections"`
	MaxConnections    int `yaml:"maxConnections"`
	KeepAliveInterval int `yaml:"keepAliveInterval"`
}

type DBConfig struct {
	Name           string               `yaml:"name"`
	User           string               `yaml:"user"`
	Password       string               `yaml:"password"`
	ReadServers    []string             `yaml:"readServers"`
	WriteServers   []string             `yaml:"writeServers"`
	SharePool      bool                 `yaml:"sharePool"`
	ConnectionPool ConnectionPoolConfig `yaml:"ConnectionPool"`
}

type ChatConfig struct {
	BannedWords    []string `yaml:"bannedWords"`
	WarningMessage string   `yaml:"warningMessage"`
}

type RosterConfig struct {
	EnforceHash bool `yaml:"enforceHash"`
	CompareHash bool `yaml:"compareHash"`
}

type RanksInterval struct {
	Days    int `yaml:"days"`
	Seconds int `yaml:"seconds"`
}

type DisconnectScore struct {
	Player   int `yaml:"player"`
	Opponent int `yaml:"opponent"`
}

type CountAsLossConfig struct {
	Enabled bool            `yaml:"Enabled"`
	Score   DisconnectScore `yaml:"Score"`
}

type DisconnectsConfig struct {
	CountAsLoss CountAsLossConfig `yaml:"CountAsLoss"`
}

type GreetingConfig struct {
	Text string `yaml:"text"`
}

// NewFeatureEntry is one entry in the NewFeatures map.
// Mirrors one element of the Python NEW_FEATURES dict.
type NewFeatureEntry struct {
	Title string `yaml:"title"`
	Text  string `yaml:"text"`
}

// ---- banned list ------------------------------------------------------------

type bannedEntry struct {
	network uint32
	mask    uint32
}

type bannedFile struct {
	Banned []string `yaml:"Banned"`
}

// ---- AdminConfig ------------------------------------------------------------

// AdminConfig holds settings from admin.yaml (separate from fiveserver.yaml).
type AdminConfig struct {
	AdminPort         int    `yaml:"AdminPort"`
	AdminUser         string `yaml:"AdminUser"`
	AdminPassword     string `yaml:"AdminPassword"`
	KeysDirectory     string `yaml:"KeysDirectory"`
	FiveserverLogFile string `yaml:"FiveserverLogFile"`
	FiveserverWebPort int    `yaml:"FiveserverWebPort"`
}

// LoadAdmin parses the admin YAML file. Returns a zero-value AdminConfig (no
// error) if the file is absent so the server can start without an admin config.
func LoadAdmin(path string) (*AdminConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return &AdminConfig{}, nil
		}
		return nil, fmt.Errorf("config: read admin %s: %w", path, err)
	}
	var ac AdminConfig
	if err := yaml.Unmarshal(data, &ac); err != nil {
		return nil, fmt.Errorf("config: parse admin %s: %w", path, err)
	}
	return &ac, nil
}

// ---- Load -------------------------------------------------------------------

// Load parses the YAML file at path into a Config. If MaxUsers is not set it
// defaults to 1000. The banned list is loaded eagerly if the file exists.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: read %s: %w", path, err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if cfg.MaxUsers == 0 {
		cfg.MaxUsers = 1000
	}
	if err := cfg.loadBannedList(); err != nil {
		// non-fatal: log-worthy but server can start without it
		_ = err
	}
	return &cfg, nil
}

// ServerIPWAN returns the resolved WAN IP. Safe for concurrent use.
func (c *Config) ServerIPWAN() string {
	c.ipMu.RLock()
	defer c.ipMu.RUnlock()
	return c.resolvedIP
}

// ResolveServerIP starts a background goroutine that resolves the WAN IP.
// If ServerIP is set explicitly it is used immediately; otherwise it fetches
// from IpDetectUri (default: http://mapote.com/cgi-bin/ip.py) and retries
// with exponential back-off (doubles each attempt, capped at 120 s) —
// matching Python FiveServerConfig.setIP exactly. Non-blocking.
func (c *Config) ResolveServerIP() {
	if c.ServerIP != "" && c.ServerIP != "auto" {
		c.ipMu.Lock()
		c.resolvedIP = c.ServerIP
		c.ipMu.Unlock()
		logger.Infof("fiveserver: server IP: %s", c.resolvedIP)
		return
	}
	uri := c.IpDetectUri
	if uri == "" {
		uri = "http://mapote.com/cgi-bin/ip.py"
	}
	go func() {
		hc := &http.Client{Timeout: 10 * time.Second}
		retryDelay := time.Second
		for {
			resp, err := hc.Get(uri)
			if err == nil {
				body, rerr := io.ReadAll(resp.Body)
				resp.Body.Close()
				if rerr == nil {
					ip := strings.TrimSpace(string(body))
					c.ipMu.Lock()
					c.resolvedIP = ip
					c.ipMu.Unlock()
					logger.Infof("fiveserver: server IP: %s", ip)
					return
				}
				err = rerr
			}
			retryDelay = min(retryDelay*2, 120*time.Second)
			logger.Warnf("fiveserver: failed to determine server IP-address (ERROR: %v). Trying again in %d seconds", err, int(retryDelay.Seconds()))
			time.Sleep(retryDelay)
		}
	}()
}

// loadBannedList reads Config.BannedList YAML and builds fastBanned.
// Mirrors Python FiveServerConfig.makeFastBannedList exactly.
func (c *Config) loadBannedList() error {
	path := c.BannedList
	if path == "" {
		return nil
	}
	if !isAbsPath(path) {
		fsroot := os.Getenv("FSROOT")
		if fsroot == "" {
			fsroot = "."
		}
		path = fsroot + "/" + path
	}
	data, err := os.ReadFile(path)
	if err != nil {
		// absent file is not an error
		return nil
	}
	var bf bannedFile
	if err := yaml.Unmarshal(data, &bf); err != nil {
		return fmt.Errorf("config: parse banned list: %w", err)
	}
	c.fastBanned = parseBannedSpecs(bf.Banned)
	return nil
}

func isAbsPath(p string) bool {
	return len(p) > 0 && (p[0] == '/' || (len(p) > 1 && p[1] == ':'))
}

// parseBannedSpecs converts CIDR/IP spec strings into (network, mask) pairs.
func parseBannedSpecs(specs []string) []bannedEntry {
	var out []bannedEntry
	for _, spec := range specs {
		// Try standard CIDR first
		if _, ipNet, err := net.ParseCIDR(spec); err == nil {
			network := binary.BigEndian.Uint32(ipNet.IP.To4())
			mask := binary.BigEndian.Uint32(ipNet.Mask)
			out = append(out, bannedEntry{network, mask})
			continue
		}
		// Plain IP address
		if ip := net.ParseIP(spec); ip != nil {
			network := binary.BigEndian.Uint32(ip.To4())
			out = append(out, bannedEntry{network, 0xFFFFFFFF})
			continue
		}
	}
	return out
}

// IsBanned returns true if ipAddress matches any entry in the banned list.
// Mirrors Python FiveServerConfig.isBanned().
func (c *Config) IsBanned(ipAddress string) bool {
	ip := net.ParseIP(ipAddress)
	if ip == nil {
		return false
	}
	ipInt := binary.BigEndian.Uint32(ip.To4())
	c.mu.RLock()
	entries := c.fastBanned
	c.mu.RUnlock()
	for _, entry := range entries {
		if (entry.network & entry.mask) == (ipInt & entry.mask) {
			return true
		}
	}
	return false
}

// Reload re-reads the YAML at filePath and updates all live-reloadable fields
// atomically. Returns a list of change descriptions and any parse error.
// Fields that cannot change at runtime (ports, DB, network) are ignored.
func (c *Config) Reload(filePath string) ([]string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("config: reload read %s: %w", filePath, err)
	}
	var fresh Config
	if err := yaml.Unmarshal(data, &fresh); err != nil {
		return nil, fmt.Errorf("config: reload parse %s: %w", filePath, err)
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	var changes []string

	if c.Debug != fresh.Debug {
		changes = append(changes, fmt.Sprintf("Debug: %v -> %v", c.Debug, fresh.Debug))
		c.Debug = fresh.Debug
	}
	if c.Log.Level != fresh.Log.Level {
		changes = append(changes, fmt.Sprintf("Log.Level: %q -> %q", c.Log.Level, fresh.Log.Level))
		c.Log.Level = fresh.Log.Level
	}
	if c.Log.File != fresh.Log.File {
		changes = append(changes, fmt.Sprintf("Log.File: %q -> %q", c.Log.File, fresh.Log.File))
		c.Log.File = fresh.Log.File
	}
	if c.ServerName != fresh.ServerName {
		changes = append(changes, fmt.Sprintf("ServerName: %q -> %q", c.ServerName, fresh.ServerName))
		c.ServerName = fresh.ServerName
	}
	if c.Greeting.Text != fresh.Greeting.Text {
		changes = append(changes, "Greeting.Text changed")
		c.Greeting = fresh.Greeting
	}
	if c.MaxUsers != fresh.MaxUsers {
		changes = append(changes, fmt.Sprintf("MaxUsers: %d -> %d", c.MaxUsers, fresh.MaxUsers))
		c.MaxUsers = fresh.MaxUsers
	}
	if c.StoreSettings != fresh.StoreSettings {
		changes = append(changes, fmt.Sprintf("StoreSettings: %v -> %v", c.StoreSettings, fresh.StoreSettings))
		c.StoreSettings = fresh.StoreSettings
	}
	if c.ShowStats != fresh.ShowStats {
		changes = append(changes, fmt.Sprintf("ShowStats: %v -> %v", c.ShowStats, fresh.ShowStats))
		c.ShowStats = fresh.ShowStats
	}
	if c.Disconnects != fresh.Disconnects {
		changes = append(changes, fmt.Sprintf("Disconnects.CountAsLoss.Enabled: %v -> %v",
			c.Disconnects.CountAsLoss.Enabled, fresh.Disconnects.CountAsLoss.Enabled))
		c.Disconnects = fresh.Disconnects
	}
	if c.Roster != fresh.Roster {
		changes = append(changes, "Roster settings changed")
		c.Roster = fresh.Roster
	}
	if c.Chat.WarningMessage != fresh.Chat.WarningMessage || !stringSlicesEqual(c.Chat.BannedWords, fresh.Chat.BannedWords) {
		changes = append(changes, "Chat settings changed")
		c.Chat = fresh.Chat
	}
	if lobbiesChanged(c.Lobbies, fresh.Lobbies) {
		changes = append(changes, fmt.Sprintf("Lobbies: %d -> %d entries", len(c.Lobbies), len(fresh.Lobbies)))
		c.Lobbies = fresh.Lobbies
	}
	if c.BannedList != fresh.BannedList {
		changes = append(changes, fmt.Sprintf("BannedList: %q -> %q", c.BannedList, fresh.BannedList))
		c.BannedList = fresh.BannedList
	}
	// Always reload banned entries in case the file on disk changed.
	if loadErr := c.loadBannedListLocked(); loadErr != nil {
		changes = append(changes, fmt.Sprintf("BannedList reload error: %v", loadErr))
	}
	c.NewFeatures = fresh.NewFeatures

	return changes, nil
}

// loadBannedListLocked reloads fastBanned from disk. Must be called with c.mu write-locked.
func (c *Config) loadBannedListLocked() error {
	path := c.BannedList
	if path == "" {
		return nil
	}
	if !isAbsPath(path) {
		fsroot := os.Getenv("FSROOT")
		if fsroot == "" {
			fsroot = "."
		}
		path = fsroot + "/" + path
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil // absent file is not an error
	}
	var bf bannedFile
	if err := yaml.Unmarshal(data, &bf); err != nil {
		return fmt.Errorf("config: parse banned list: %w", err)
	}
	c.fastBanned = parseBannedSpecs(bf.Banned)
	return nil
}

func stringSlicesEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func lobbiesChanged(a, b []Lobby) bool {
	if len(a) != len(b) {
		return true
	}
	for i := range a {
		if a[i].Name != b[i].Name || a[i].Type != b[i].Type || a[i].TypeCode != b[i].TypeCode {
			return true
		}
	}
	return false
}
