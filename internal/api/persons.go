package api

import (
	"net/http"

	"github.com/benny-conn/solo-trace/internal/store"
	"github.com/gin-gonic/gin"
)

type createPersonRequest struct {
	Name       string `json:"name" binding:"required"`
	Instrument string `json:"instrument"`
}

type updatePersonRequest struct {
	Name       string `json:"name"`
	Instrument string `json:"instrument"`
}

func (h *Handler) CreatePerson(c *gin.Context) {
	var req createPersonRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	if req.Instrument == "" {
		req.Instrument = "unknown"
	}

	person, err := h.Store.CreatePerson(c.Request.Context(), store.CreatePersonParams{
		ID:         generateID(),
		Name:       req.Name,
		Instrument: req.Instrument,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusCreated, person)
}

func (h *Handler) GetPerson(c *gin.Context) {
	person, err := h.Store.GetPerson(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "person not found"})
		return
	}
	c.JSON(http.StatusOK, person)
}

func (h *Handler) ListPersons(c *gin.Context) {
	persons, err := h.Store.ListPersons(c.Request.Context())
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, persons)
}

func (h *Handler) UpdatePerson(c *gin.Context) {
	existing, err := h.Store.GetPerson(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "person not found"})
		return
	}

	var req updatePersonRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	name := existing.Name
	if req.Name != "" {
		name = req.Name
	}
	instrument := existing.Instrument
	if req.Instrument != "" {
		instrument = req.Instrument
	}

	person, err := h.Store.UpdatePerson(c.Request.Context(), store.UpdatePersonParams{
		ID:         existing.ID,
		Name:       name,
		Instrument: instrument,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, person)
}
