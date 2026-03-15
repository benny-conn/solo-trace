package store

import (
	"context"
	"time"
)

// JobStatus represents the lifecycle of a processing job.
type JobStatus string

const (
	JobStatusPending    JobStatus = "pending"
	JobStatusProcessing JobStatus = "processing"
	JobStatusDone       JobStatus = "done"
	JobStatusFailed     JobStatus = "failed"
)

// ── Domain models ─────────────────────────────────────────────────────────────

type Person struct {
	ID         string
	Name       string
	Instrument string
	CreatedAt  time.Time
	UpdatedAt  time.Time
}

type Job struct {
	ID                   string
	PersonID             string
	VideoURL             string
	VideoTitle           *string
	Status               JobStatus
	ErrorMessage         *string
	VideoDurationSeconds *float64
	StartTimeOffset      *string
	CreatedAt            time.Time
	UpdatedAt            time.Time
}

type Clip struct {
	ID                   string
	JobID                string
	PersonID             string
	ClipIndex            int
	StartTime            float64
	EndTime              float64
	Duration             float64
	R2VideoKey           *string
	R2VideoURL           *string
	R2MidiKey            *string
	R2MidiURL            *string
	AudioPeak            *float64
	AudioHitCount        *int
	AudioTotalWindows    *int
	AudioHitRatio        *float64
	VisualScore          *float64
	BPM                  *float64
	KeyName              *string
	Mode                 *string
	EnergyMean           *float64
	SpectralCentroidMean *float64
	NoteEvents           *string // raw JSON
	CreatedAt            time.Time
}

// ── Param types ───────────────────────────────────────────────────────────────

type CreatePersonParams struct {
	ID         string
	Name       string
	Instrument string
}

type UpdatePersonParams struct {
	ID         string
	Name       string
	Instrument string
}

type CreateJobParams struct {
	ID              string
	PersonID        string
	VideoURL        string
	StartTimeOffset *string
}

type UpdateJobParams struct {
	ID                   string
	VideoTitle           *string
	Status               JobStatus
	ErrorMessage         *string
	VideoDurationSeconds *float64
}

type CreateClipParams struct {
	ID                   string
	JobID                string
	PersonID             string
	ClipIndex            int
	StartTime            float64
	EndTime              float64
	Duration             float64
	R2VideoKey           *string
	R2VideoURL           *string
	R2MidiKey            *string
	R2MidiURL            *string
	AudioPeak            *float64
	AudioHitCount        *int
	AudioTotalWindows    *int
	AudioHitRatio        *float64
	VisualScore          *float64
	BPM                  *float64
	KeyName              *string
	Mode                 *string
	EnergyMean           *float64
	SpectralCentroidMean *float64
	NoteEvents           *string
}

// ── Store interface ───────────────────────────────────────────────────────────
// Swap implementations by providing a different Store to NewHandler.
// SQLiteStore is the current implementation; PostgresStore can be added later.

type Store interface {
	// Persons
	CreatePerson(ctx context.Context, arg CreatePersonParams) (Person, error)
	GetPerson(ctx context.Context, id string) (Person, error)
	ListPersons(ctx context.Context) ([]Person, error)
	UpdatePerson(ctx context.Context, arg UpdatePersonParams) (Person, error)

	// Jobs
	CreateJob(ctx context.Context, arg CreateJobParams) (Job, error)
	GetJob(ctx context.Context, id string) (Job, error)
	UpdateJob(ctx context.Context, arg UpdateJobParams) (Job, error)
	ListJobsByPerson(ctx context.Context, personID string) ([]Job, error)

	// Clips
	CreateClip(ctx context.Context, arg CreateClipParams) (Clip, error)
	GetClip(ctx context.Context, id string) (Clip, error)
	ListClipsByPerson(ctx context.Context, personID string) ([]Clip, error)
	ListClipsByJob(ctx context.Context, jobID string) ([]Clip, error)
	GetLatestClipByPerson(ctx context.Context, personID string) (Clip, error)
}
