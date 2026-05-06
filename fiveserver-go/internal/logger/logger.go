package logger

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const LevelCritical = slog.Level(12)

var (
	currentLevel slog.LevelVar
	global       *slog.Logger
	mu           sync.RWMutex
)

func init() {
	global = slog.New(newPlainHandler(os.Stdout, &currentLevel))
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
	global = slog.New(newPlainHandler(w, &currentLevel))
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

// plainHandler writes log records without escaping newlines in the message,
// so multiline messages (e.g. hex dumps) render as real line breaks.
type plainHandler struct {
	w     io.Writer
	level slog.Leveler
	wmu   sync.Mutex
}

func newPlainHandler(w io.Writer, level slog.Leveler) *plainHandler {
	return &plainHandler{w: w, level: level}
}

func (h *plainHandler) Enabled(_ context.Context, l slog.Level) bool {
	return l >= h.level.Level()
}

func (h *plainHandler) Handle(_ context.Context, r slog.Record) error {
	lvl := r.Level.String()
	if r.Level == LevelCritical {
		lvl = "CRITICAL"
	}
	ts := r.Time.UTC().Format(time.RFC3339Nano)
	line := fmt.Sprintf("time=%s level=%s %s\n", ts, lvl, r.Message)
	h.wmu.Lock()
	defer h.wmu.Unlock()
	_, err := io.WriteString(h.w, line)
	return err
}

func (h *plainHandler) WithAttrs(_ []slog.Attr) slog.Handler  { return h }
func (h *plainHandler) WithGroup(_ string) slog.Handler        { return h }

func get() *slog.Logger {
	mu.RLock()
	defer mu.RUnlock()
	return global
}

func Debug(msg string, args ...any)    { get().Debug(msg, args...) }
func Info(msg string, args ...any)     { get().Info(msg, args...) }
func Warn(msg string, args ...any)     { get().Warn(msg, args...) }
func Error(msg string, args ...any)    { get().Error(msg, args...) }
func Critical(msg string, args ...any) { get().Log(context.TODO(), LevelCritical, msg, args...) }

func Debugf(format string, args ...any)    { get().Debug(fmt.Sprintf(format, args...)) }
func Infof(format string, args ...any)     { get().Info(fmt.Sprintf(format, args...)) }
func Warnf(format string, args ...any)     { get().Warn(fmt.Sprintf(format, args...)) }
func Errorf(format string, args ...any)    { get().Error(fmt.Sprintf(format, args...)) }
func Criticalf(format string, args ...any) {
	get().Log(context.TODO(), LevelCritical, fmt.Sprintf(format, args...))
}
