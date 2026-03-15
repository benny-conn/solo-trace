package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/benny-conn/solo-grabber/internal/store"
	"github.com/segmentio/ksuid"
	"github.com/spf13/viper"
)

// resultClip matches the JSON structure output by process_video.py.
type resultClip struct {
	ClipIndex  int             `json:"clip_index"`
	Start      float64         `json:"start"`
	End        float64         `json:"end"`
	Duration   float64         `json:"duration"`
	R2VideoKey *string         `json:"r2_video_key"`
	R2VideoURL *string         `json:"r2_video_url"`
	Detection  struct {
		AudioPeak         *float64 `json:"audio_peak"`
		AudioHitCount     *int     `json:"audio_hit_count"`
		AudioTotalWindows *int     `json:"audio_total_windows"`
		AudioHitRatio     *float64 `json:"audio_hit_ratio"`
		VisualScore       *float64 `json:"visual_score"`
	} `json:"detection"`
	// Analysis is stored as a raw JSON blob; R2 MIDI keys are also extracted from it.
	Analysis json.RawMessage `json:"analysis"`
}

// analysisR2Fields is used only to extract upload URLs from the analysis blob.
type analysisR2Fields struct {
	MidiR2Key *string `json:"r2_midi_key"`
	MidiR2URL *string `json:"r2_midi_url"`
}

type scriptResult struct {
	VideoTitle           *string      `json:"video_title"`
	VideoDurationSeconds *float64     `json:"video_duration_seconds"`
	Clips                []resultClip `json:"clips"`
	Errors               []string     `json:"errors"`
}

// Run executes the Python processing pipeline for a job in the background.
// It updates job status in the store as it progresses.
func Run(jobID string, personID string, videoURL string, startTimeOffset *string, s store.Store) {
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
	args := buildArgs(videoURL, personID, jobID, jobDir, startTimeOffset)

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
		// Extract R2 MIDI keys from the analysis blob (populated by uploader.py)
		var r2 analysisR2Fields
		if rc.Analysis != nil {
			_ = json.Unmarshal(rc.Analysis, &r2)
		}
		analysisJSON := rawJSONString(rc.Analysis)

		if _, err := s.CreateClip(ctx, store.CreateClipParams{
			ID:                ksuid.New().String(),
			JobID:             jobID,
			PersonID:          personID,
			ClipIndex:         rc.ClipIndex,
			StartTime:         rc.Start,
			EndTime:           rc.End,
			Duration:          rc.Duration,
			R2VideoKey:        rc.R2VideoKey,
			R2VideoURL:        rc.R2VideoURL,
			R2MidiKey:         r2.MidiR2Key,
			R2MidiURL:         r2.MidiR2URL,
			AudioPeak:         rc.Detection.AudioPeak,
			AudioHitCount:     rc.Detection.AudioHitCount,
			AudioTotalWindows: rc.Detection.AudioTotalWindows,
			AudioHitRatio:     rc.Detection.AudioHitRatio,
			VisualScore:       rc.Detection.VisualScore,
			Analysis:          analysisJSON,
		}); err != nil {
			log.Printf("[runner] job %s: failed to save clip %d: %v", jobID, rc.ClipIndex, err)
		}
	}

	log.Printf("[runner] job %s: done — %d clip(s) saved", jobID, len(result.Clips))

	// Clean up job directory — videos, clips, and stems are large and no longer
	// needed once clips are uploaded to R2 and metadata is in the DB.
	if err := os.RemoveAll(jobDir); err != nil {
		log.Printf("[runner] job %s: warning — could not clean up job dir: %v", jobID, err)
	}
}

func buildArgs(videoURL, personID, jobID, jobDir string, startTimeOffset *string) []string {
	scriptPath := viper.GetString("PYTHON_SCRIPT_PATH")

	args := []string{
		scriptPath,
		"--video", videoURL,
		"--person-id", personID,
		"--job-id", jobID,
		"--output-dir", jobDir,
		"--reference-audio", viper.GetString("REFERENCE_AUDIO_PATH"),
	}

	// Audio detection thresholds
	if thresh := viper.GetString("AUDIO_THRESHOLD"); thresh != "" {
		args = append(args, "--similarity-threshold", thresh)
	}
	if minPeak := viper.GetString("MIN_PEAK"); minPeak != "" {
		args = append(args, "--min-peak", minPeak)
	}

	// Start time: job-level override takes precedence over global default
	startTime := viper.GetString("DEFAULT_START_TIME")
	if startTimeOffset != nil && *startTimeOffset != "" {
		startTime = *startTimeOffset
	}
	if startTime != "" {
		args = append(args, "--start-time", startTime)
	}

	// Visual check: enabled automatically when reference images are present
	if refDir := viper.GetString("REFERENCE_IMAGES_DIR"); refDir != "" {
		imgs := findReferenceImages(refDir)
		if len(imgs) > 0 {
			args = append(args, "--visual-check")
			args = append(args, "--reference-images")
			args = append(args, imgs...)
			if vt := viper.GetString("VISUAL_THRESHOLD"); vt != "" {
				args = append(args, "--visual-threshold", vt)
			}
		}
	}

	if viper.GetString("R2_ACCOUNT_ID") == "" {
		args = append(args, "--skip-upload")
	}

	return args
}

// findReferenceImages returns paths to all image files in dir.
func findReferenceImages(dir string) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		log.Printf("[runner] could not read REFERENCE_IMAGES_DIR %q: %v", dir, err)
		return nil
	}
	imageExts := map[string]bool{".jpg": true, ".jpeg": true, ".png": true, ".webp": true}
	var imgs []string
	for _, e := range entries {
		if !e.IsDir() && imageExts[strings.ToLower(filepath.Ext(e.Name()))] {
			imgs = append(imgs, filepath.Join(dir, e.Name()))
		}
	}
	return imgs
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

// rawJSONString converts a json.RawMessage to a *string for DB storage.
func rawJSONString(raw json.RawMessage) *string {
	if raw == nil {
		return nil
	}
	s := string(raw)
	return &s
}
