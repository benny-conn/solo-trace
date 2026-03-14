package utils

import (
	"fmt"

	"github.com/spf13/viper"
)

func LoadConfig() {
	viper.SetDefault("ENVIRONMENT", "local")
	viper.SetDefault("SERVER_PORT", 4000)

	// SQLite
	viper.SetDefault("DB_PATH", "./solo-grabber.db")

	// Job runner
	viper.SetDefault("JOBS_DIR", "./jobs")
	viper.SetDefault("PYTHON_BIN", "python")
	viper.SetDefault("PYTHON_SCRIPT_PATH", "./scripts/process_video.py")

	// Cloudflare R2 (optional — omit to skip uploads)
	viper.SetDefault("R2_ACCOUNT_ID", "")
	viper.SetDefault("R2_ACCESS_KEY_ID", "")
	viper.SetDefault("R2_SECRET_ACCESS_KEY", "")
	viper.SetDefault("R2_BUCKET", "")
	viper.SetDefault("R2_PUBLIC_BASE_URL", "")

	// Audio fingerprint (optional — path to JSON built by build_fingerprint.py)
	viper.SetDefault("FINGERPRINT_PATH", "")

	// Nightly Smalls cron
	viper.SetDefault("SMALLS_CRON_ENABLED", false)
	viper.SetDefault("SMALLS_CRON_SCHEDULE", "CRON_TZ=America/New_York 0 0 5 * * *")

	// API key auth (skip in local env)
	viper.SetDefault("API_KEY", "")

	viper.AutomaticEnv()
}

func ServerAddr() string {
	return fmt.Sprintf(":%d", viper.GetInt("SERVER_PORT"))
}
