package api

import (
	"net/http"

	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/gin-gonic/gin"
	"github.com/segmentio/ksuid"
)

// Handler holds all dependencies for API route handlers.
type Handler struct {
	Store store.Store
}

func NewHandler(s store.Store) *Handler {
	return &Handler{Store: s}
}

func SetupRoutes(r *gin.Engine, h *Handler) {
	r.Use(corsMiddleware())

	r.GET("/health", h.HealthCheck)

	api := r.Group("/api", APIKeyMiddleware())
	{
		api.POST("/persons", h.CreatePerson)
		api.GET("/persons", h.ListPersons)
		api.GET("/persons/:id", h.GetPerson)
		api.PATCH("/persons/:id", h.UpdatePerson)

		api.POST("/jobs", h.CreateJob)
		api.GET("/jobs/:id", h.GetJob)
		api.GET("/persons/:id/jobs", h.ListJobsByPerson)

		api.GET("/performances", h.ListPerformances)
		api.GET("/performances/latest", h.GetLatestPerformance)
		api.GET("/performances/:id", h.GetPerformance)
		api.GET("/persons/:id/performances", h.ListPerformancesByPerson)
	}
}

func (h *Handler) HealthCheck(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func generateID() string {
	return ksuid.New().String()
}

func corsMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}
