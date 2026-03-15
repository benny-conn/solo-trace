package api

import (
	"net/http"

	"github.com/benny-conn/solo-grabber/internal/runner"
	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/gin-gonic/gin"
)

type createJobRequest struct {
	PersonID        string  `json:"person_id" binding:"required"`
	VideoURL        string  `json:"video_url" binding:"required"`
	StartTimeOffset *string `json:"start_time_offset"`
}

func (h *Handler) CreateJob(c *gin.Context) {
	var req createJobRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Verify person exists
	if _, err := h.Store.GetPerson(c.Request.Context(), req.PersonID); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "person not found"})
		return
	}

	job, err := h.Store.CreateJob(c.Request.Context(), store.CreateJobParams{
		ID:              generateID(),
		PersonID:        req.PersonID,
		VideoURL:        req.VideoURL,
		StartTimeOffset: req.StartTimeOffset,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// Run pipeline in background — returns immediately, job status reflects progress
	go runner.Run(job.ID, req.PersonID, req.VideoURL, job.StartTimeOffset, h.Store)

	c.JSON(http.StatusCreated, job)
}

func (h *Handler) GetJob(c *gin.Context) {
	job, err := h.Store.GetJob(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "job not found"})
		return
	}
	c.JSON(http.StatusOK, job)
}

func (h *Handler) ListJobsByPerson(c *gin.Context) {
	jobs, err := h.Store.ListJobsByPerson(c.Request.Context(), c.Param("id"))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, jobs)
}
