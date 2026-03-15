package utils

import (
	"fmt"

	"github.com/spf13/viper"
)

func LoadConfig() {
	viper.SetDefault("ENVIRONMENT", "local")
	viper.SetDefault("SERVER_PORT", 4000)

	// SQLite
	viper.SetDefault("DB_PATH", "./solo-trace.db")

	// Job runner
	viper.SetDefault("JOBS_DIR", "./jobs")
	viper.SetDefault("PYTHON_BIN", "python")
	viper.SetDefault("PYTHON_SCRIPT_PATH", "./scripts/process_video.py")
	viper.SetDefault("REFERENCE_AUDIO_PATH", "./scripts/me_stems/horn_mixed.wav")
	viper.SetDefault("REFERENCE_IMAGES_DIR", "./scripts/references")
	viper.SetDefault("AUDIO_THRESHOLD", "")
	viper.SetDefault("MIN_PEAK", "")
	viper.SetDefault("DEFAULT_START_TIME", "")
	viper.SetDefault("VISUAL_THRESHOLD", "")
	viper.SetDefault("YTDLP_COOKIES_FILE", "")

	// Cloudflare R2 (optional — omit to skip uploads)
	viper.SetDefault("R2_ACCOUNT_ID", "")
	viper.SetDefault("R2_ACCESS_KEY_ID", "")
	viper.SetDefault("R2_SECRET_ACCESS_KEY", "")
	viper.SetDefault("R2_BUCKET", "")
	viper.SetDefault("R2_PUBLIC_BASE_URL", "")

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
