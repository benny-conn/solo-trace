package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/segmentio/ksuid"
	"github.com/spf13/viper"
)

// resultClip matches the JSON structure output by process_video.py.
type resultClip struct {
	ClipIndex int     `json:"clip_index"`
	Start     float64 `json:"start"`
	End       float64 `json:"end"`
	Duration  float64 `json:"duration"`
	R2VideoKey *string `json:"r2_video_key"`
	R2VideoURL *string `json:"r2_video_url"`
	Analysis  struct {
		BPM                  *float64 `json:"bpm"`
		KeyName              *string  `json:"key"`
		Mode                 *string  `json:"mode"`
		EnergyMean           *float64 `json:"energy_mean"`
		SpectralCentroidMean *float64 `json:"spectral_centroid_mean"`
		MidiR2Key            *string  `json:"r2_midi_key"`
		MidiR2URL            *string  `json:"r2_midi_url"`
		NoteEvents           any      `json:"note_events"`
	} `json:"analysis"`
}

type scriptResult struct {
	VideoTitle           *string      `json:"video_title"`
	VideoDurationSeconds *float64     `json:"video_duration_seconds"`
	Clips                []resultClip `json:"clips"`
	Errors               []string     `json:"errors"`
}

// Run executes the Python processing pipeline for a job in the background.
// It updates job status in the store as it progresses.
func Run(jobID string, personID string, videoURL string, referencePhotoPath string, s store.Store) {
	ctx := context.Background()

	// Mark as processing
	if err := updateStatus(ctx, s, jobID, store.JobStatusProcessing, nil); err != nil {
		log.Printf("[runner] job %s: failed to set processing status: %v", jobID, err)
		return
	}

	jobDir := filepath.Join(viper.GetString("JOBS_DIR"), jobID)
	if err := os.MkdirAll(jobDir, 0755); err != nil {
		setFailed(ctx, s, jobID, fmt.Sprintf("failed to create job dir: %v", err))
		return
	}

	resultPath := filepath.Join(jobDir, "result.json")
	args := buildArgs(videoURL, referencePhotoPath, personID, jobDir)

	log.Printf("[runner] job %s: starting Python pipeline", jobID)
	cmd := exec.Command(viper.GetString("PYTHON_BIN"), args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		setFailed(ctx, s, jobID, fmt.Sprintf("script error: %v", err))
		return
	}

	result, err := parseResult(resultPath)
	if err != nil {
		setFailed(ctx, s, jobID, fmt.Sprintf("parse result: %v", err))
		return
	}

	// Update job with video metadata
	if _, err := s.UpdateJob(ctx, store.UpdateJobParams{
		ID:                   jobID,
		VideoTitle:           result.VideoTitle,
		Status:               store.JobStatusDone,
		VideoDurationSeconds: result.VideoDurationSeconds,
	}); err != nil {
		log.Printf("[runner] job %s: failed to update job metadata: %v", jobID, err)
	}

	// Save clips
	for _, rc := range result.Clips {
		noteEventsJSON := marshalNoteEvents(rc.Analysis.NoteEvents)
		if _, err := s.CreateClip(ctx, store.CreateClipParams{
			ID:                   ksuid.New().String(),
			JobID:                jobID,
			PersonID:             personID,
			ClipIndex:            rc.ClipIndex,
			StartTime:            rc.Start,
			EndTime:              rc.End,
			Duration:             rc.Duration,
			R2VideoKey:           rc.R2VideoKey,
			R2VideoURL:           rc.R2VideoURL,
			R2MidiKey:            rc.Analysis.MidiR2Key,
			R2MidiURL:            rc.Analysis.MidiR2URL,
			BPM:                  rc.Analysis.BPM,
			KeyName:              rc.Analysis.KeyName,
			Mode:                 rc.Analysis.Mode,
			EnergyMean:           rc.Analysis.EnergyMean,
			SpectralCentroidMean: rc.Analysis.SpectralCentroidMean,
			NoteEvents:           noteEventsJSON,
		}); err != nil {
			log.Printf("[runner] job %s: failed to save clip %d: %v", jobID, rc.ClipIndex, err)
		}
	}

	log.Printf("[runner] job %s: done — %d clip(s) saved", jobID, len(result.Clips))
}

func buildArgs(videoURL, referencePhotoPath, personID, jobDir string) []string {
	scriptPath := viper.GetString("PYTHON_SCRIPT_PATH")

	args := []string{
		scriptPath,
		"--video-url", videoURL,
		"--reference-photo", referencePhotoPath,
		"--person-id", personID,
		"--output-dir", jobDir,
	}

	if viper.GetString("R2_ACCOUNT_ID") == "" {
		args = append(args, "--skip-upload")
	}

	// Use audio fingerprint if one has been built for this person
	fingerprintPath := viper.GetString("FINGERPRINT_PATH")
	if fingerprintPath != "" {
		args = append(args, "--fingerprint", fingerprintPath)
	}

	return args
}

func parseResult(path string) (*scriptResult, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read result.json: %w", err)
	}
	var result scriptResult
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("unmarshal result.json: %w", err)
	}
	return &result, nil
}

func updateStatus(ctx context.Context, s store.Store, jobID string, status store.JobStatus, errMsg *string) error {
	_, err := s.UpdateJob(ctx, store.UpdateJobParams{
		ID:           jobID,
		Status:       status,
		ErrorMessage: errMsg,
	})
	return err
}

func setFailed(ctx context.Context, s store.Store, jobID string, msg string) {
	log.Printf("[runner] job %s: failed — %s", jobID, msg)
	if err := updateStatus(ctx, s, jobID, store.JobStatusFailed, &msg); err != nil {
		log.Printf("[runner] job %s: could not persist failure: %v", jobID, err)
	}
}

func marshalNoteEvents(v any) *string {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	s := string(b)
	return &s
}
