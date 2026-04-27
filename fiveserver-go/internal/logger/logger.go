package logger

import (
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
)

const LevelCritical = slog.Level(12)

var (
	currentLevel slog.LevelVar
	global       *slog.Logger
	mu           sync.RWMutex
	initOnce     sync.Once
)

func init() {
	global = slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level:       &currentLevel,
		ReplaceAttr: replaceAttr,
	}))
	currentLevel.Set(slog.LevelInfo)
}

// Init sets up the logger with the given level and optional file path.
// If filePath is empty, logs go to stdout only. Safe to call multiple times.
func Init(level, filePath string) error {
	mu.Lock()
	defer mu.Unlock()

	var w io.Writer = os.Stdout

	if filePath != "" {
		if err := os.MkdirAll(filepath.Dir(filePath), 0755); err != nil {
			return fmt.Errorf("logger: mkdir %s: %w", filepath.Dir(filePath), err)
		}
		f, err := os.OpenFile(filePath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
		if err != nil {
			return fmt.Errorf("logger: open %s: %w", filePath, err)
		}
		w = io.MultiWriter(os.Stdout, f)
	}

	currentLevel.Set(parseLevel(level))
	global = slog.New(slog.NewTextHandler(w, &slog.HandlerOptions{
		Level:       &currentLevel,
		ReplaceAttr: replaceAttr,
	}))
	return nil
}

// SetLevel changes the minimum log level at runtime without reopening the file.
func SetLevel(level string) {
	currentLevel.Set(parseLevel(level))
}

func parseLevel(level string) slog.Level {
	switch level {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	case "critical":
		return LevelCritical
	default:
		return slog.LevelInfo
	}
}

func replaceAttr(_ []string, a slog.Attr) slog.Attr {
	if a.Key == slog.LevelKey {
		if lvl, ok := a.Value.Any().(slog.Level); ok && lvl == LevelCritical {
			a.Value = slog.StringValue("CRITICAL")
		}
	}
	return a
}

func get() *slog.Logger {
	mu.RLock()
	defer mu.RUnlock()
	return global
}

func Debug(msg string, args ...any)    { get().Debug(msg, args...) }
func Info(msg string, args ...any)     { get().Info(msg, args...) }
func Warn(msg string, args ...any)     { get().Warn(msg, args...) }
func Error(msg string, args ...any)    { get().Error(msg, args...) }
func Critical(msg string, args ...any) { get().Log(nil, LevelCritical, msg, args...) }

func Debugf(format string, args ...any)    { get().Debug(fmt.Sprintf(format, args...)) }
func Infof(format string, args ...any)     { get().Info(fmt.Sprintf(format, args...)) }
func Warnf(format string, args ...any)     { get().Warn(fmt.Sprintf(format, args...)) }
func Errorf(format string, args ...any)    { get().Error(fmt.Sprintf(format, args...)) }
func Criticalf(format string, args ...any) { get().Log(nil, LevelCritical, fmt.Sprintf(format, args...)) }
