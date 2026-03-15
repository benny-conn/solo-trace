-- solo-trace SQLite schema
-- Reference file — SQLiteStore applies this on startup via db.Exec

create table if not exists persons (
    id          text primary key,
    name        text not null,
    instrument  text not null default 'unknown',
    created_at  datetime not null default (datetime('now')),
    updated_at  datetime not null default (datetime('now'))
);

create table if not exists jobs (
    id                      text primary key,
    person_id               text not null references persons(id),
    video_url               text not null,
    video_title             text,
    status                  text not null default 'pending', -- pending | processing | done | failed
    error_message           text,
    video_duration_seconds  real,
    start_time_offset       text,
    created_at              datetime not null default (datetime('now')),
    updated_at              datetime not null default (datetime('now'))
);

create table if not exists clips (
    id                      text primary key,
    job_id                  text not null references jobs(id),
    person_id               text not null references persons(id),
    clip_index              integer not null,
    start_time              real not null,
    end_time                real not null,
    duration                real not null,
    r2_video_key            text,
    r2_video_url            text,
    r2_midi_key             text,
    r2_midi_url             text,
    -- detection metadata
    audio_peak              real,
    audio_hit_count         integer,
    audio_total_windows     integer,
    audio_hit_ratio         real,
    visual_score            real,
    -- analysis (JSON blob: note_events, note_count, most_common_notes, etc.)
    analysis                text,
    created_at              datetime not null default (datetime('now'))
);

create index if not exists idx_jobs_person_id    on jobs(person_id);
create index if not exists idx_clips_job_id      on clips(job_id);
create index if not exists idx_clips_person_id   on clips(person_id);
create index if not exists idx_clips_created_at  on clips(created_at desc);
