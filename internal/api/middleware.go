package api

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/spf13/viper"
)

// APIKeyMiddleware checks the X-API-Key header.
// In local environment, auth is skipped entirely.
func APIKeyMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if viper.GetString("ENVIRONMENT") == "local" {
			c.Next()
			return
		}

		key := c.GetHeader("X-API-Key")
		if key == "" {
			key = c.Query("api_key")
		}

		if key == "" || key != viper.GetString("API_KEY") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid or missing API key"})
			return
		}

		c.Next()
	}
}
