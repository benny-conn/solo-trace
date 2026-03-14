package api

import (
	"database/sql"
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
)

// "Performances" are clips — the public-facing name for the API consumers.

func (h *Handler) ListPerformances(c *gin.Context) {
	personID := c.Query("person_id")

	if personID != "" {
		clips, err := h.Store.ListClipsByPerson(c.Request.Context(), personID)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusOK, clips)
		return
	}

	// No filter — return all clips across all persons via each person's list.
	// This is fine for a personal project with few persons.
	persons, err := h.Store.ListPersons(c.Request.Context())
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	all := make([]any, 0)
	for _, p := range persons {
		clips, err := h.Store.ListClipsByPerson(c.Request.Context(), p.ID)
		if err != nil {
			continue
		}
		for _, cl := range clips {
			all = append(all, cl)
		}
	}
	c.JSON(http.StatusOK, all)
}

func (h *Handler) GetLatestPerformance(c *gin.Context) {
	personID := c.Query("person_id")
	if personID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "person_id query param required"})
		return
	}

	clip, err := h.Store.GetLatestClipByPerson(c.Request.Context(), personID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(http.StatusNotFound, gin.H{"error": "no performances found for this person"})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, clip)
}

func (h *Handler) GetPerformance(c *gin.Context) {
	clip, err := h.Store.GetClip(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "performance not found"})
		return
	}
	c.JSON(http.StatusOK, clip)
}

func (h *Handler) ListPerformancesByPerson(c *gin.Context) {
	clips, err := h.Store.ListClipsByPerson(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, clips)
}
