package store

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// SQLiteStore implements Store using a local SQLite database file.
type SQLiteStore struct {
	db *sql.DB
}

// NewSQLiteStore opens (or creates) a SQLite database at dbPath and applies
// the schema. It returns a ready-to-use SQLiteStore.
func NewSQLiteStore(dbPath string) (*SQLiteStore, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}

	// SQLite performs best with a single writer; WAL mode allows concurrent reads.
	if _, err := db.Exec("PRAGMA journal_mode=WAL;"); err != nil {
		return nil, fmt.Errorf("enable WAL: %w", err)
	}
	if _, err := db.Exec("PRAGMA foreign_keys=ON;"); err != nil {
		return nil, fmt.Errorf("enable foreign keys: %w", err)
	}

	s := &SQLiteStore{db: db}
	if err := s.migrate(); err != nil {
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return s, nil
}

func (s *SQLiteStore) migrate() error {
	schema, err := os.ReadFile("sql/schema/schema.sql")
	if err != nil {
		// Fall back to inline schema if file not found (e.g. in container)
		schema = []byte(inlineSchema)
	}
	if _, err = s.db.Exec(string(schema)); err != nil {
		return err
	}
	// Additive alterations: ignored if the column already exists (fresh schema includes it).
	for _, alt := range []string{
		`alter table jobs add column video_upload_date text`,
	} {
		if _, err := s.db.Exec(alt); err != nil && !strings.Contains(err.Error(), "duplicate column name") {
			return err
		}
	}
	return nil
}

// ── Persons ───────────────────────────────────────────────────────────────────

func (s *SQLiteStore) CreatePerson(ctx context.Context, arg CreatePersonParams) (Person, error) {
	now := time.Now().UTC()
	_, err := s.db.ExecContext(ctx,
		`insert into persons (id, name, instrument, created_at, updated_at)
		 values (?, ?, ?, ?, ?)`,
		arg.ID, arg.Name, arg.Instrument, now, now,
	)
	if err != nil {
		return Person{}, err
	}
	return s.GetPerson(ctx, arg.ID)
}

func (s *SQLiteStore) GetPerson(ctx context.Context, id string) (Person, error) {
	row := s.db.QueryRowContext(ctx,
		`select id, name, instrument, created_at, updated_at
		 from persons where id = ?`, id)
	return scanPerson(row)
}

func (s *SQLiteStore) ListPersons(ctx context.Context) ([]Person, error) {
	rows, err := s.db.QueryContext(ctx,
		`select id, name, instrument, created_at, updated_at
		 from persons order by created_at desc`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collectPersons(rows)
}

func (s *SQLiteStore) UpdatePerson(ctx context.Context, arg UpdatePersonParams) (Person, error) {
	_, err := s.db.ExecContext(ctx,
		`update persons set name=?, instrument=?, updated_at=? where id=?`,
		arg.Name, arg.Instrument, time.Now().UTC(), arg.ID,
	)
	if err != nil {
		return Person{}, err
	}
	return s.GetPerson(ctx, arg.ID)
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

func (s *SQLiteStore) CreateJob(ctx context.Context, arg CreateJobParams) (Job, error) {
	now := time.Now().UTC()
	_, err := s.db.ExecContext(ctx,
		`insert into jobs (id, person_id, video_url, start_time_offset, status, created_at, updated_at)
		 values (?, ?, ?, ?, 'pending', ?, ?)`,
		arg.ID, arg.PersonID, arg.VideoURL, arg.StartTimeOffset, now, now,
	)
	if err != nil {
		return Job{}, err
	}
	return s.GetJob(ctx, arg.ID)
}

func (s *SQLiteStore) GetJob(ctx context.Context, id string) (Job, error) {
	row := s.db.QueryRowContext(ctx,
		`select id, person_id, video_url, video_title, video_upload_date, status, error_message,
		        video_duration_seconds, start_time_offset, created_at, updated_at
		 from jobs where id = ?`, id)
	return scanJob(row)
}

func (s *SQLiteStore) UpdateJob(ctx context.Context, arg UpdateJobParams) (Job, error) {
	_, err := s.db.ExecContext(ctx,
		`update jobs
		 set video_title=?, video_upload_date=?, status=?, error_message=?, video_duration_seconds=?, updated_at=?
		 where id=?`,
		arg.VideoTitle, arg.VideoUploadDate, string(arg.Status), arg.ErrorMessage, arg.VideoDurationSeconds,
		time.Now().UTC(), arg.ID,
	)
	if err != nil {
		return Job{}, err
	}
	return s.GetJob(ctx, arg.ID)
}

func (s *SQLiteStore) ListJobsByPerson(ctx context.Context, personID string) ([]Job, error) {
	rows, err := s.db.QueryContext(ctx,
		`select id, person_id, video_url, video_title, video_upload_date, status, error_message,
		        video_duration_seconds, start_time_offset, created_at, updated_at
		 from jobs where person_id = ? order by created_at desc`, personID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collectJobs(rows)
}

// ── Clips ─────────────────────────────────────────────────────────────────────

func (s *SQLiteStore) CreateClip(ctx context.Context, arg CreateClipParams) (Clip, error) {
	now := time.Now().UTC()
	_, err := s.db.ExecContext(ctx,
		`insert into clips (
			id, job_id, person_id, clip_index, start_time, end_time, duration,
			r2_video_key, r2_video_url, r2_midi_key, r2_midi_url,
			audio_peak, audio_hit_count, audio_total_windows, audio_hit_ratio, visual_score,
			analysis, created_at
		) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		arg.ID, arg.JobID, arg.PersonID, arg.ClipIndex,
		arg.StartTime, arg.EndTime, arg.Duration,
		arg.R2VideoKey, arg.R2VideoURL, arg.R2MidiKey, arg.R2MidiURL,
		arg.AudioPeak, arg.AudioHitCount, arg.AudioTotalWindows, arg.AudioHitRatio, arg.VisualScore,
		arg.Analysis, now,
	)
	if err != nil {
		return Clip{}, err
	}
	return s.GetClip(ctx, arg.ID)
}

func (s *SQLiteStore) GetClip(ctx context.Context, id string) (Clip, error) {
	row := s.db.QueryRowContext(ctx, clipSelectCols+` where id = ?`, id)
	return scanClip(row)
}

func (s *SQLiteStore) ListClipsByPerson(ctx context.Context, personID string) ([]Clip, error) {
	rows, err := s.db.QueryContext(ctx, clipSelectCols+` where person_id = ? order by created_at desc`, personID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collectClips(rows)
}

func (s *SQLiteStore) ListClipsByJob(ctx context.Context, jobID string) ([]Clip, error) {
	rows, err := s.db.QueryContext(ctx, clipSelectCols+` where job_id = ? order by clip_index asc`, jobID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collectClips(rows)
}

func (s *SQLiteStore) GetLatestClipByPerson(ctx context.Context, personID string) (Clip, error) {
	row := s.db.QueryRowContext(ctx, clipSelectCols+` where person_id = ? order by created_at desc limit 1`, personID)
	return scanClip(row)
}

// ── Scan helpers ──────────────────────────────────────────────────────────────

const clipSelectCols = `
	select id, job_id, person_id, clip_index, start_time, end_time, duration,
	       r2_video_key, r2_video_url, r2_midi_key, r2_midi_url,
	       audio_peak, audio_hit_count, audio_total_windows, audio_hit_ratio, visual_score,
	       analysis, created_at
	from clips`

type scanner interface {
	Scan(dest ...any) error
}

func scanPerson(s scanner) (Person, error) {
	var p Person
	err := s.Scan(&p.ID, &p.Name, &p.Instrument, &p.CreatedAt, &p.UpdatedAt)
	return p, err
}

func scanJob(s scanner) (Job, error) {
	var j Job
	var status string
	err := s.Scan(
		&j.ID, &j.PersonID, &j.VideoURL, &j.VideoTitle, &j.VideoUploadDate,
		&status, &j.ErrorMessage, &j.VideoDurationSeconds,
		&j.StartTimeOffset, &j.CreatedAt, &j.UpdatedAt,
	)
	j.Status = JobStatus(status)
	return j, err
}

func scanClip(s scanner) (Clip, error) {
	var c Clip
	err := s.Scan(
		&c.ID, &c.JobID, &c.PersonID, &c.ClipIndex,
		&c.StartTime, &c.EndTime, &c.Duration,
		&c.R2VideoKey, &c.R2VideoURL, &c.R2MidiKey, &c.R2MidiURL,
		&c.AudioPeak, &c.AudioHitCount, &c.AudioTotalWindows, &c.AudioHitRatio, &c.VisualScore,
		&c.Analysis, &c.CreatedAt,
	)
	return c, err
}

func collectPersons(rows *sql.Rows) ([]Person, error) {
	var out []Person
	for rows.Next() {
		p, err := scanPerson(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

func collectJobs(rows *sql.Rows) ([]Job, error) {
	var out []Job
	for rows.Next() {
		j, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, rows.Err()
}

func collectClips(rows *sql.Rows) ([]Clip, error) {
	var out []Clip
	for rows.Next() {
		c, err := scanClip(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// inlineSchema is used as fallback when schema.sql is not on disk (container).
const inlineSchema = `
create table if not exists persons (
	id text primary key, name text not null, instrument text not null default 'unknown',
	created_at datetime not null default (datetime('now')),
	updated_at datetime not null default (datetime('now'))
);
create table if not exists jobs (
	id text primary key, person_id text not null references persons(id),
	video_url text not null, video_title text, video_upload_date text,
	status text not null default 'pending', error_message text,
	video_duration_seconds real, start_time_offset text,
	created_at datetime not null default (datetime('now')),
	updated_at datetime not null default (datetime('now'))
);
create table if not exists clips (
	id text primary key, job_id text not null references jobs(id),
	person_id text not null references persons(id), clip_index integer not null,
	start_time real not null, end_time real not null, duration real not null,
	r2_video_key text, r2_video_url text, r2_midi_key text, r2_midi_url text,
	audio_peak real, audio_hit_count integer, audio_total_windows integer,
	audio_hit_ratio real, visual_score real,
	analysis text, created_at datetime not null default (datetime('now'))
);
create index if not exists idx_jobs_person_id   on jobs(person_id);
create index if not exists idx_clips_job_id     on clips(job_id);
create index if not exists idx_clips_person_id  on clips(person_id);
create index if not exists idx_clips_created_at on clips(created_at desc);
`
