# Solo Trace

A personal tool that automatically finds, clips, and transcribes every jazz solo I play from live concert recordings.

Jazz venues like Smalls Jazz Club in NYC stream their shows on YouTube. As a trombonist who plays there regularly, I'd otherwise need to scrub through hours of footage to find my own solos. Solo Trace handles that automatically every night.

---

## How It Works

A nightly cron job scrapes Smalls Jazz Club's website for the previous night's stream, then runs each video through a multi-stage pipeline:

1. **Download** the live jam session video via yt-dlp
2. **Audio scan** using a [CLAP](https://github.com/LAION-AI/CLAP) neural embedding model, comparing every 2-second window against a reference recording of my playing to find matching segments
3. **Visual verification** using [CLIP](https://github.com/openai/CLIP) and face recognition to confirm I appear on screen during those segments (e.g. holding the trombone)
4. **Source separation** via [Demucs](https://github.com/facebookresearch/demucs) (Meta) to isolate the brass/wind stem from the full band mix
5. **MIDI transcription** using [Basic Pitch](https://github.com/spotify/basic-pitch) (Spotify) to extract note events, pitch statistics, and phrase analysis
6. **Upload** video clips and MIDI files to Cloudflare R2
7. **Store** everything in SQLite: clip metadata, timestamps, audio scores, visual scores, and transcription data

The output is a REST API serving timestamped video clips with note-level transcriptions.

---

## Tech Stack

**Backend**

- Go + Gin
- SQLite
- robfig/cron

**ML Pipeline** (Python)

- CLAP (audio similarity embeddings)
- CLIP + face_recognition (visual verification)
- Demucs (neural source separation)
- Basic Pitch (polyphonic pitch detection)
- yt-dlp

**Infrastructure**

- Apple M4 Mac Mini (always-on home server)
- Cloudflare Tunnel (public API exposure without open ports)
- Cloudflare R2 (object storage for clips and MIDI files)
- launchd (process management)

---

## API

See [API.md](./API.md) for full documentation. Endpoints cover persons, jobs, and performances (clips).

All `/api/*` routes require `X-API-Key` header authentication.

```
GET  /health
POST /api/jobs
GET  /api/jobs/:id
GET  /api/performances
GET  /api/performances/:id
GET  /api/performances/latest?person_id=
GET  /api/persons/:id/performances
```

---

## What Gets Stored Per Clip

| Field          | Description                                            |
| -------------- | ------------------------------------------------------ |
| Video clip     | MP4 with direct R2 URL                                 |
| MIDI file      | Full transcription with direct R2 URL                  |
| Timestamps     | Start, end, duration within source video               |
| Audio peak     | Highest CLAP similarity score in segment               |
| Visual score   | Face recognition confidence                            |
| Note count     | Total notes in the solo                                |
| Pitch range    | Lowest to highest note (e.g. F3 to Bb5)                |
| Common notes   | Most frequently played pitch classes                   |
| Note density   | Notes per second                                       |
| Longest phrase | Note count and duration of the longest unbroken phrase |

---

## The Technical Challenge

Finding a single trombone in a live jazz band recording (drums, bass, piano, saxophone, vocals) is harder than it sounds. There is no labeled training data for a specific player, the audio is a noisy stream recording, and the instrument blends into the band mix.

The approach combines three independent signals:

- **Audio embeddings** (CLAP): instead of detecting the instrument directly, the model computes similarity between 2-second windows and a reference recording of my playing. Segments in the same embedding space are candidates.
- **Source separation** (Demucs): strips the mix down to the brass stem before transcription, improving MIDI accuracy significantly.
- **Visual confirmation** (CLIP + face recognition): filters out false positives by checking that I actually appear on camera during the flagged segments.

None of these signals is reliable alone. Together they are precise enough to run unattended every night.
