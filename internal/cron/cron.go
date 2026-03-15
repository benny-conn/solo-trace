package cron

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os/exec"

	"github.com/benny-conn/solo-trace/internal/runner"
	"github.com/benny-conn/solo-trace/internal/store"
	"github.com/robfig/cron/v3"
	"github.com/segmentio/ksuid"
	"github.com/spf13/viper"
)

// scraperResult matches the --json output of smalls_scraper.py --run
type scraperResult struct {
	Video *struct {
		URL   string `json:"url"`
		Title string `json:"title"`
	} `json:"video"`
	Lineup *struct {
		EventTitle string `json:"event_title"`
		Artists    []struct {
			Name       string `json:"name"`
			Instrument string `json:"instrument"`
		} `json:"artists"`
	} `json:"lineup"`
}

// Start registers the nightly Smalls cron job and starts the scheduler.
// Schedule: 5AM US Eastern every day.
func Start(s store.Store) *cron.Cron {
	// Use EST/EDT location via TZ offset — robfig/cron supports CRON_TZ
	c := cron.New(cron.WithSeconds())

	schedule := viper.GetString("SMALLS_CRON_SCHEDULE")
	if schedule == "" {
		schedule = "CRON_TZ=America/New_York 0 0 5 * * *" // 5:00 AM ET
	}

	c.AddFunc(schedule, func() { //nolint:errcheck
		log.Println("[cron] Starting nightly Smalls run")
		if err := runNightly(s); err != nil {
			log.Printf("[cron] Nightly run failed: %v", err)
		}
	})

	c.Start()
	log.Printf("[cron] Nightly Smalls job scheduled: %s", schedule)
	return c
}

func runNightly(s store.Store) error {
	// ── 1. Run the Python scraper to get last night's video URL ───────────────
	python := viper.GetString("PYTHON_BIN")
	scriptPath := viper.GetString("PYTHON_SCRIPT_PATH")
	scraperPath := scriptPath[:len(scriptPath)-len("process_video.py")] + "smalls_scraper.py"

	out, err := exec.Command(python, scraperPath, "--run", "--json").Output()
	if err != nil {
		return fmt.Errorf("scraper failed: %w", err)
	}

	var result scraperResult
	if err := json.Unmarshal(out, &result); err != nil {
		return fmt.Errorf("parse scraper output: %w", err)
	}

	if result.Video == nil || result.Video.URL == "" {
		log.Println("[cron] No video found for last night — skipping job submission")
		return nil
	}

	videoURL := result.Video.URL
	log.Printf("[cron] Found video: %s", videoURL)

	// ── 2. Find configured persons to process ─────────────────────────────────
	persons, err := s.ListPersons(context.Background())
	if err != nil {
		return fmt.Errorf("list persons: %w", err)
	}

	if len(persons) == 0 {
		log.Println("[cron] No persons configured — skipping job submission")
		return nil
	}

	// ── 3. Submit a job per person ────────────────────────────────────────────
	defaultStartTime := viper.GetString("DEFAULT_START_TIME")
	var startTimeOffset *string
	if defaultStartTime != "" {
		startTimeOffset = &defaultStartTime
	}

	for _, person := range persons {
		job, err := s.CreateJob(context.Background(), store.CreateJobParams{
			ID:              ksuid.New().String(),
			PersonID:        person.ID,
			VideoURL:        videoURL,
			StartTimeOffset: startTimeOffset,
		})
		if err != nil {
			log.Printf("[cron] Failed to create job for %s: %v", person.Name, err)
			continue
		}

		log.Printf("[cron] Submitted job %s for %s (%s)", job.ID, person.Name, videoURL)
		go runner.Run(job.ID, person.ID, videoURL, job.StartTimeOffset, s)
	}

	return nil
}
