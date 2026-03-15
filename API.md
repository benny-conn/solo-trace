# Solo Trace API

Base URL: `https://solo.bennyconn.com`

All `/api/*` routes require an API key passed as a header:
```
X-API-Key: <key>
```
Or as a query param: `?api_key=<key>`

---

## Health

### `GET /health`
No auth required.

**Response**
```json
{ "status": "ok" }
```

---

## Persons

A person represents a musician being tracked.

### `POST /api/persons`
Create a person.

**Request body**
```json
{
  "name": "Benny Conn",        // required
  "instrument": "trombone"     // optional, defaults to "unknown"
}
```

**Response** `201`
```json
{
  "ID": "2abc123...",
  "Name": "Benny Conn",
  "Instrument": "trombone",
  "CreatedAt": "2026-03-15T05:00:00Z",
  "UpdatedAt": "2026-03-15T05:00:00Z"
}
```

---

### `GET /api/persons`
List all persons.

**Response** `200` — array of person objects (same shape as above)

---

### `GET /api/persons/:id`
Get a single person by ID.

**Response** `200` — person object, or `404`

---

### `PATCH /api/persons/:id`
Update a person's name or instrument. Only provided fields are changed.

**Request body**
```json
{
  "name": "Benjamin Conn",
  "instrument": "trombone"
}
```

**Response** `200` — updated person object

---

## Jobs

A job processes a YouTube video to find and extract solo clips for a person.
It runs asynchronously — submit it and poll until `Status` is `done` or `failed`.

### `POST /api/jobs`
Submit a video processing job.

**Request body**
```json
{
  "person_id": "2abc123...",           // required — must exist
  "video_url": "https://youtube.com/watch?v=...",  // required
  "start_time_offset": "1:00:00"       // optional — skip to this timestamp before scanning
}
```

**Response** `201`
```json
{
  "ID": "3xyz789...",
  "PersonID": "2abc123...",
  "VideoURL": "https://youtube.com/watch?v=...",
  "VideoTitle": null,
  "Status": "pending",
  "ErrorMessage": null,
  "VideoDurationSeconds": null,
  "StartTimeOffset": "1:00:00",
  "CreatedAt": "2026-03-15T05:00:00Z",
  "UpdatedAt": "2026-03-15T05:00:00Z"
}
```

**Job statuses:** `pending` → `processing` → `done` | `failed`

---

### `GET /api/jobs/:id`
Poll a job's status.

**Response** `200`
```json
{
  "ID": "3xyz789...",
  "PersonID": "2abc123...",
  "VideoURL": "https://youtube.com/watch?v=...",
  "VideoTitle": "Jazz Concert 2026",
  "Status": "done",
  "ErrorMessage": null,
  "VideoDurationSeconds": 7234.5,
  "StartTimeOffset": "1:00:00",
  "CreatedAt": "2026-03-15T05:00:00Z",
  "UpdatedAt": "2026-03-15T05:45:00Z"
}
```

---

### `GET /api/persons/:id/jobs`
List all jobs for a person, newest first.

**Response** `200` — array of job objects

---

## Performances

Performances are the extracted solo clips produced by a completed job.
Each job can produce multiple clips.

### `GET /api/performances`
List performances. Optionally filter by person.

**Query params**
- `person_id` (optional) — filter to a specific person

**Response** `200` — array of performance objects

---

### `GET /api/performances/latest`
Get the most recently extracted clip for a person.

**Query params**
- `person_id` (required)

**Response** `200` — single performance object, or `404` if none exist

---

### `GET /api/performances/:id`
Get a single performance by ID.

**Response** `200` — performance object, or `404`

---

### `GET /api/persons/:id/performances`
List all performances for a person, newest first.

**Response** `200` — array of performance objects

---

### Performance object
```json
{
  "ID": "4def456...",
  "JobID": "3xyz789...",
  "PersonID": "2abc123...",
  "ClipIndex": 0,
  "StartTime": 2252.0,
  "EndTime": 2284.0,
  "Duration": 32.0,
  "R2VideoKey": "clips/4def456.mp4",
  "R2VideoURL": "https://solotrace.bennyconn.com/clips/4def456.mp4",
  "R2MidiKey": "midi/4def456.mid",
  "R2MidiURL": "https://solotrace.bennyconn.com/midi/4def456.mid",
  "AudioPeak": 0.823,
  "AudioHitCount": 11,
  "AudioTotalWindows": 29,
  "AudioHitRatio": 0.379,
  "VisualScore": 0.72,
  "Analysis": {
    "note_count": 84,
    "most_common_notes": [
      { "note": "Bb", "count": 12 },
      { "note": "F",  "count": 10 }
    ],
    "highest_note": "Bb5",
    "lowest_note": "F3",
    "pitch_range": 29,
    "avg_note_duration_s": 0.21,
    "note_density_per_s": 2.4,
    "longest_phrase_notes": 18,
    "longest_phrase_duration_s": 6.5
  },
  "CreatedAt": "2026-03-15T05:45:00Z"
}
```

**Notes:**
- `R2VideoURL` / `R2MidiURL` — direct public URLs to the video clip and MIDI file in R2 storage. May be `null` if upload failed.
- `Analysis` — parsed from a JSON string; contains pitch transcription stats. May be `null` if transcription failed.
- `AudioPeak` — highest CLAP similarity score in the segment (0–1)
- `VisualScore` — face recognition confidence that the person appears in the clip (0–1). May be `null` if visual check was skipped.
