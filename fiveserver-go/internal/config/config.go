package config

import (
	"encoding/binary"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// ---- top-level config -------------------------------------------------------

type Config struct {
	ServerIP             string              `yaml:"ServerIP"`
	ListenOn             string              `yaml:"ListenOn"`
	IpDetectUri          string              `yaml:"IpDetectUri"`
	Lobbies              []Lobby             `yaml:"Lobbies"`
	GamePorts            GamePorts           `yaml:"GamePorts"`
	NetworkServer        NetworkServerConfig `yaml:"NetworkServer"`
	WebInterface         WebInterfaceConfig  `yaml:"WebInterface"`
	Debug                bool                `yaml:"Debug"`
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

	// runtime-only: parsed banned list entries (net, mask) in host byte order
	fastBanned []bannedEntry
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
			l.Type = "restricted"
		}
	default:
		return fmt.Errorf("config: unexpected lobby YAML node kind %v", value.Kind)
	}
	l.TypeCode = lobbyTypeCode(l.Type, l.TypeList)
	return nil
}

// lobbyTypeCode matches Python FiveServerConfig.__init__ lobby-type logic.
func lobbyTypeCode(typ string, list []string) byte {
	switch typ {
	case "noStats":
		return 0x20
	case "restricted":
		divMap := map[string]int{"A": 0, "3B": 1, "3A": 2, "2": 3, "1": 4}
		code := 0
		for _, d := range list {
			if v, ok := divMap[d]; ok {
				code += 1 << v
			}
		}
		return byte(code)
	default: // "open" or anything else
		return 0x5f
	}
}

type GamePorts struct {
	PES5  int `yaml:"pes5"`
	WE9   int `yaml:"we9"`
	WE9LE int `yaml:"we9le"`
}

type NetworkServerConfig struct {
	MainService        int            `yaml:"mainService"`
	NetworkMenuService int            `yaml:"networkMenuService"`
	LoginService       map[string]int `yaml:"loginService"`
}

type WebInterfaceConfig struct {
	Port int `yaml:"port"`
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

// ---- banned list ------------------------------------------------------------

type bannedEntry struct {
	network uint32
	mask    uint32
}

type bannedFile struct {
	Banned []string `yaml:"Banned"`
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

// ResolveServerIP resolves cfg.ServerIP when it is "auto" or empty.
// Fetches the WAN address from cfg.IpDetectUri, retrying with exponential
// back-off (delay doubles each attempt, capped at 120 s) until it succeeds —
// matching Python FiveServerConfig.setIP exactly.
func (c *Config) ResolveServerIP() {
	if c.ServerIP != "" && c.ServerIP != "auto" {
		log.Printf("fiveserver: server IP: %s", c.ServerIP)
		return
	}
	uri := c.IpDetectUri
	if uri == "" {
		log.Printf("fiveserver: WARNING: ServerIP is 'auto' but IpDetectUri is not configured")
		return
	}
	hc := &http.Client{Timeout: 10 * time.Second}
	retryDelay := time.Second
	for {
		resp, err := hc.Get(uri)
		if err == nil {
			body, rerr := io.ReadAll(resp.Body)
			resp.Body.Close()
			if rerr == nil {
				c.ServerIP = strings.TrimSpace(string(body))
				log.Printf("fiveserver: server IP: %s", c.ServerIP)
				return
			}
			err = rerr
		}
		retryDelay = min(retryDelay*2, 120*time.Second)
		log.Printf("fiveserver: failed to determine server IP-address (ERROR: %v). Trying again in %s", err, retryDelay)
		time.Sleep(retryDelay)
	}
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
	for _, entry := range c.fastBanned {
		if (entry.network & entry.mask) == (ipInt & entry.mask) {
			return true
		}
	}
	return false
}
