package api

import (
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"

	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/gin-gonic/gin"
	"github.com/spf13/viper"
)

type createPersonRequest struct {
	Name               string  `json:"name" binding:"required"`
	Instrument         string  `json:"instrument"`
	ReferencePhotoURL  *string `json:"reference_photo_url"`
}

type updatePersonRequest struct {
	Name               string  `json:"name"`
	Instrument         string  `json:"instrument"`
	ReferencePhotoURL  *string `json:"reference_photo_url"`
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

	id := generateID()
	var photoPath *string

	if req.ReferencePhotoURL != nil && *req.ReferencePhotoURL != "" {
		path, err := downloadPhoto(*req.ReferencePhotoURL, id)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "could not download reference photo: " + err.Error()})
			return
		}
		photoPath = &path
	}

	person, err := h.Store.CreatePerson(c.Request.Context(), store.CreatePersonParams{
		ID:                 id,
		Name:               req.Name,
		Instrument:         req.Instrument,
		ReferencePhotoPath: photoPath,
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
	photoPath := existing.ReferencePhotoPath
	if req.ReferencePhotoURL != nil && *req.ReferencePhotoURL != "" {
		path, err := downloadPhoto(*req.ReferencePhotoURL, existing.ID)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "could not download reference photo: " + err.Error()})
			return
		}
		photoPath = &path
	}

	person, err := h.Store.UpdatePerson(c.Request.Context(), store.UpdatePersonParams{
		ID:                 existing.ID,
		Name:               name,
		Instrument:         instrument,
		ReferencePhotoPath: photoPath,
	})
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, person)
}

// downloadPhoto fetches a photo from a URL and saves it to the photos directory.
func downloadPhoto(rawURL string, personID string) (string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return "", &ErrUnsafeURL{URL: rawURL}
	}

	photosDir := filepath.Join(viper.GetString("JOBS_DIR"), "photos")
	if err := os.MkdirAll(photosDir, 0755); err != nil {
		return "", err
	}

	// Detect extension from URL path, default to .jpg
	ext := filepath.Ext(parsed.Path)
	if ext == "" {
		ext = ".jpg"
	}
	destPath := filepath.Join(photosDir, personID+ext)

	resp, err := http.Get(rawURL) //nolint:gosec — URL validated above
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	// Sanity check content type
	ct := resp.Header.Get("Content-Type")
	if ct != "" && ct != "image/jpeg" && ct != "image/png" && ct != "image/webp" {
		return "", &ErrUnsafeURL{URL: rawURL, Reason: "unexpected content-type: " + ct}
	}

	f, err := os.Create(destPath)
	if err != nil {
		return "", err
	}
	defer f.Close()

	if _, err := io.Copy(f, io.LimitReader(resp.Body, 10<<20)); err != nil { // 10MB max
		return "", err
	}

	return destPath, nil
}

type ErrUnsafeURL struct {
	URL    string
	Reason string
}

func (e *ErrUnsafeURL) Error() string {
	if e.Reason != "" {
		return "unsafe URL: " + e.Reason
	}
	return "unsafe URL: " + e.URL
}
