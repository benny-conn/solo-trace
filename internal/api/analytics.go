package api

import (
	"encoding/json"
	"math"
	"net/http"
	"sort"
	"time"

	"github.com/benny-conn/solo-trace/internal/store"
	"github.com/gin-gonic/gin"
)

// ── Response types ────────────────────────────────────────────────────────────

type PersonAnalytics struct {
	PersonID     string           `json:"person_id"`
	Attendance   AttendanceStats  `json:"attendance"`
	Music        MusicStats       `json:"music"`
	Performances PerformanceStats `json:"performances"`
}

type AttendanceStats struct {
	TotalGigs       int          `json:"total_gigs"`
	FirstGig        *string      `json:"first_gig"`
	MostRecentGig   *string      `json:"most_recent_gig"`
	LongestStreak   int          `json:"longest_streak_days"`
	BusiestWeek     *BusiestWeek `json:"busiest_week"`
	GigsByMonth     []MonthCount `json:"gigs_by_month"`
	GigsByDayOfWeek []DayCount   `json:"gigs_by_day_of_week"`
}

type BusiestWeek struct {
	WeekOf string `json:"week_of"` // Monday of that week, YYYY-MM-DD
	Count  int    `json:"count"`
}

type MonthCount struct {
	Month string `json:"month"` // YYYY-MM
	Count int    `json:"count"`
}

type DayCount struct {
	Day   string `json:"day"`
	Count int    `json:"count"`
}

type MusicStats struct {
	TotalNotes                int           `json:"total_notes"`
	ClipsAnalyzed             int           `json:"clips_analyzed"`
	ClipsMissingAnalysis      int           `json:"clips_missing_analysis"`
	AvgNotesPerClip           float64       `json:"avg_notes_per_clip"`
	TopNotes                  []NoteCount   `json:"top_notes"`
	HighestNoteEver           *string       `json:"highest_note_ever"`
	LowestNoteEver            *string       `json:"lowest_note_ever"`
	AvgPitchRangeSemitones    *float64      `json:"avg_pitch_range_semitones"`
	WidestPitchRangeSemitones *int          `json:"widest_pitch_range_semitones"`
	AvgNoteDensityPerS        *float64      `json:"avg_note_density_per_s"`
	AvgNoteDurationS          *float64      `json:"avg_note_duration_s"`
	LongestPhrase             *PhraseRecord `json:"longest_phrase"`
}

type NoteCount struct {
	Note  string `json:"note"`
	Count int    `json:"count"`
}

type PhraseRecord struct {
	Notes    int     `json:"notes"`
	Duration float64 `json:"duration_s"`
	ClipID   string  `json:"clip_id"`
}

type PerformanceStats struct {
	TotalClips           int      `json:"total_clips"`
	TotalDurationSeconds float64  `json:"total_duration_seconds"`
	AvgDurationSeconds   float64  `json:"avg_duration_seconds"`
	AvgAudioPeak         *float64 `json:"avg_audio_peak"`
	PeakClipID           *string  `json:"peak_clip_id"`
}

// clipAnalysisBlob mirrors the JSON structure stored in clips.analysis.
type clipAnalysisBlob struct {
	NoteCount          int         `json:"note_count"`
	MostCommonNotes    []NoteCount `json:"most_common_notes"`
	HighestNote        *string     `json:"highest_note"`
	LowestNote         *string     `json:"lowest_note"`
	PitchRange         *int        `json:"pitch_range"`
	AvgNoteDurationS   *float64    `json:"avg_note_duration_s"`
	NoteDensityPerS    *float64    `json:"note_density_per_s"`
	LongestPhraseNotes *int        `json:"longest_phrase_notes"`
	LongestPhraseDurS  *float64    `json:"longest_phrase_duration_s"`
}

// ── Handler ───────────────────────────────────────────────────────────────────

func (h *Handler) GetPersonAnalytics(c *gin.Context) {
	ctx := c.Request.Context()
	personID := c.Param("id")

	if _, err := h.Store.GetPerson(ctx, personID); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "person not found"})
		return
	}

	jobs, err := h.Store.ListJobsByPerson(ctx, personID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	clips, err := h.Store.ListClipsByPerson(ctx, personID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, PersonAnalytics{
		PersonID:     personID,
		Attendance:   computeAttendance(jobs),
		Music:        computeMusic(clips),
		Performances: computePerformances(clips),
	})
}

// ── Attendance ────────────────────────────────────────────────────────────────

func computeAttendance(jobs []store.Job) AttendanceStats {
	// One gig = one distinct date. Multiple jobs on the same date collapse to one.
	dateSet := map[string]struct{}{}
	for _, j := range jobs {
		if j.Status == store.JobStatusDone && j.VideoUploadDate != nil {
			dateSet[*j.VideoUploadDate] = struct{}{}
		}
	}
	if len(dateSet) == 0 {
		return AttendanceStats{}
	}

	dates := make([]time.Time, 0, len(dateSet))
	for d := range dateSet {
		if t, err := time.Parse("2006-01-02", d); err == nil {
			dates = append(dates, t)
		}
	}
	sort.Slice(dates, func(i, j int) bool { return dates[i].Before(dates[j]) })

	first := dates[0].Format("2006-01-02")
	last := dates[len(dates)-1].Format("2006-01-02")

	return AttendanceStats{
		TotalGigs:       len(dates),
		FirstGig:        &first,
		MostRecentGig:   &last,
		LongestStreak:   longestConsecutiveStreak(dates),
		BusiestWeek:     busiestWeek(dates),
		GigsByMonth:     gigsByMonth(dates),
		GigsByDayOfWeek: gigsByDayOfWeek(dates),
	}
}

func longestConsecutiveStreak(dates []time.Time) int {
	if len(dates) == 0 {
		return 0
	}
	best, current := 1, 1
	for i := 1; i < len(dates); i++ {
		days := int(math.Round(dates[i].Sub(dates[i-1]).Hours() / 24))
		if days == 1 {
			current++
			if current > best {
				best = current
			}
		} else {
			current = 1
		}
	}
	return best
}

func busiestWeek(dates []time.Time) *BusiestWeek {
	type weekKey struct{ year, week int }
	counts := map[weekKey]int{}
	mondays := map[weekKey]time.Time{}

	for _, d := range dates {
		year, week := d.ISOWeek()
		k := weekKey{year, week}
		counts[k]++
		if _, seen := mondays[k]; !seen {
			// Rewind to Monday of this ISO week
			wd := int(d.Weekday())
			if wd == 0 {
				wd = 7 // Sunday
			}
			mondays[k] = d.AddDate(0, 0, -(wd - 1))
		}
	}

	var best weekKey
	bestCount := 0
	for k, c := range counts {
		if c > bestCount {
			bestCount = c
			best = k
		}
	}
	if bestCount == 0 {
		return nil
	}
	weekOf := mondays[best].Format("2006-01-02")
	return &BusiestWeek{WeekOf: weekOf, Count: bestCount}
}

func gigsByMonth(dates []time.Time) []MonthCount {
	counts := map[string]int{}
	for _, d := range dates {
		counts[d.Format("2006-01")]++
	}
	months := make([]MonthCount, 0, len(counts))
	for m, c := range counts {
		months = append(months, MonthCount{Month: m, Count: c})
	}
	sort.Slice(months, func(i, j int) bool { return months[i].Month < months[j].Month })
	return months
}

func gigsByDayOfWeek(dates []time.Time) []DayCount {
	counts := map[string]int{}
	for _, d := range dates {
		counts[d.Weekday().String()]++
	}
	result := make([]DayCount, 0, len(counts))
	for day, c := range counts {
		result = append(result, DayCount{Day: day, Count: c})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Count > result[j].Count })
	return result
}

// ── Music ─────────────────────────────────────────────────────────────────────

func computeMusic(clips []store.Clip) MusicStats {
	var (
		totalNotes  int
		analyzed    int
		missing     int
		allNotes    = map[string]int{}
		pitchRanges []int
		densities   []float64
		durations   []float64
		highestMIDI = -1
		highestNote *string
		lowestMIDI  = 999
		lowestNote  *string
		longest     *PhraseRecord
	)

	for _, cl := range clips {
		if cl.Analysis == nil {
			missing++
			continue
		}
		var blob clipAnalysisBlob
		if err := json.Unmarshal([]byte(*cl.Analysis), &blob); err != nil {
			missing++
			continue
		}
		analyzed++
		totalNotes += blob.NoteCount

		for _, nc := range blob.MostCommonNotes {
			allNotes[nc.Note] += nc.Count
		}
		if blob.PitchRange != nil {
			pitchRanges = append(pitchRanges, *blob.PitchRange)
		}
		if blob.NoteDensityPerS != nil {
			densities = append(densities, *blob.NoteDensityPerS)
		}
		if blob.AvgNoteDurationS != nil {
			durations = append(durations, *blob.AvgNoteDurationS)
		}
		if blob.HighestNote != nil {
			if midi := noteNameToMIDI(*blob.HighestNote); midi > highestMIDI {
				highestMIDI = midi
				highestNote = blob.HighestNote
			}
		}
		if blob.LowestNote != nil {
			if midi := noteNameToMIDI(*blob.LowestNote); midi >= 0 && midi < lowestMIDI {
				lowestMIDI = midi
				lowestNote = blob.LowestNote
			}
		}
		if blob.LongestPhraseNotes != nil && blob.LongestPhraseDurS != nil {
			if longest == nil || *blob.LongestPhraseNotes > longest.Notes {
				id := cl.ID
				longest = &PhraseRecord{
					Notes:    *blob.LongestPhraseNotes,
					Duration: *blob.LongestPhraseDurS,
					ClipID:   id,
				}
			}
		}
	}

	var avgNotesPerClip float64
	if analyzed > 0 {
		avgNotesPerClip = math.Round(float64(totalNotes)/float64(analyzed)*10) / 10
	}

	var avgPitchRange *float64
	var widestPitchRange *int
	if len(pitchRanges) > 0 {
		sum, best := 0, pitchRanges[0]
		for _, r := range pitchRanges {
			sum += r
			if r > best {
				best = r
			}
		}
		v := math.Round(float64(sum)/float64(len(pitchRanges))*10) / 10
		avgPitchRange = &v
		widestPitchRange = &best
	}

	var avgDensity *float64
	if len(densities) > 0 {
		sum := 0.0
		for _, d := range densities {
			sum += d
		}
		v := math.Round(sum/float64(len(densities))*100) / 100
		avgDensity = &v
	}

	var avgDuration *float64
	if len(durations) > 0 {
		sum := 0.0
		for _, d := range durations {
			sum += d
		}
		v := math.Round(sum/float64(len(durations))*1000) / 1000
		avgDuration = &v
	}

	if highestMIDI == -1 {
		highestNote = nil
	}
	if lowestMIDI == 999 {
		lowestNote = nil
	}

	return MusicStats{
		TotalNotes:                totalNotes,
		ClipsAnalyzed:             analyzed,
		ClipsMissingAnalysis:      missing,
		AvgNotesPerClip:           avgNotesPerClip,
		TopNotes:                  topNotesByCount(allNotes, 7),
		HighestNoteEver:           highestNote,
		LowestNoteEver:            lowestNote,
		AvgPitchRangeSemitones:    avgPitchRange,
		WidestPitchRangeSemitones: widestPitchRange,
		AvgNoteDensityPerS:        avgDensity,
		AvgNoteDurationS:          avgDuration,
		LongestPhrase:             longest,
	}
}

func topNotesByCount(counts map[string]int, n int) []NoteCount {
	result := make([]NoteCount, 0, len(counts))
	for note, count := range counts {
		result = append(result, NoteCount{Note: note, Count: count})
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Count > result[j].Count })
	if len(result) > n {
		result = result[:n]
	}
	return result
}

// noteNameToMIDI converts a note name like "Bb4" or "F#3" to a MIDI pitch number.
// Returns -1 on parse failure.
func noteNameToMIDI(name string) int {
	if len(name) < 2 {
		return -1
	}
	semitones := map[byte]int{'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
	base, ok := semitones[name[0]]
	if !ok {
		return -1
	}
	i := 1
	if i < len(name) && name[i] == '#' {
		base++
		i++
	} else if i < len(name) && name[i] == 'b' {
		base--
		i++
	}
	if i >= len(name) || name[i] < '0' || name[i] > '9' {
		return -1
	}
	octave := int(name[i] - '0')
	return (octave+1)*12 + base
}

// ── Performances ──────────────────────────────────────────────────────────────

func computePerformances(clips []store.Clip) PerformanceStats {
	if len(clips) == 0 {
		return PerformanceStats{}
	}

	var totalDur, peakSum float64
	var peakCount int
	var bestPeak float64
	var bestPeakID *string

	for _, cl := range clips {
		totalDur += cl.Duration
		if cl.AudioPeak != nil {
			peakSum += *cl.AudioPeak
			peakCount++
			if *cl.AudioPeak > bestPeak {
				bestPeak = *cl.AudioPeak
				id := cl.ID
				bestPeakID = &id
			}
		}
	}

	var avgPeak *float64
	if peakCount > 0 {
		v := math.Round(peakSum/float64(peakCount)*1000) / 1000
		avgPeak = &v
	}

	return PerformanceStats{
		TotalClips:           len(clips),
		TotalDurationSeconds: math.Round(totalDur*100) / 100,
		AvgDurationSeconds:   math.Round(totalDur/float64(len(clips))*100) / 100,
		AvgAudioPeak:         avgPeak,
		PeakClipID:           bestPeakID,
	}
}
