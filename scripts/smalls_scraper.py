"""
smalls_scraper.py

Two responsibilities per nightly run (called at ~5AM EST):

  1. FIND LAST NIGHT'S VIDEO
     Load yesterday's stored jam lineup (headliner name + date).
     Search YouTube for "{headliner} Smalls Jazz Club {date}" → much more
     precise than a generic search.

  2. SCRAPE TONIGHT'S UPCOMING JAM
     Hit the Smalls AJAX calendar API for today's events.
     Find the open jam session (ends 3:30–4AM, description contains "open jam").
     Extract musician lineup (name + instrument).
     Save to jam_history.json keyed by date.

Usage:
  # Full nightly run — does both, prints yesterday's video URL to stdout
  python smalls_scraper.py --run

  # Only scrape tonight's lineup (no YouTube search)
  python smalls_scraper.py --scrape-upcoming [--date 2026-03-14]

  # Only find YouTube video for a specific date
  python smalls_scraper.py --find-video 2026-03-14

  # Print stored lineup for a date
  python smalls_scraper.py --show 2026-03-14

  # JSON output (for piping into process_video.py or the Go runner)
  python smalls_scraper.py --run --json
"""

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SMALLS_BASE = "https://www.smallslive.com"
AJAX_URL = f"{SMALLS_BASE}/search/upcoming-ajax/"
DEFAULT_HISTORY_FILE = Path(__file__).parent / "jam_history.json"

# End times that indicate the late-night open jam (3:00 AM – 4:30 AM)
LATE_NIGHT_END_HOURS = {3, 4}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.smallslive.com/",
    "X-Requested-With": "XMLHttpRequest",
}


# ── Smallslive calendar scraping ──────────────────────────────────────────────

def fetch_events_for_date(target_date: date) -> list[dict]:
    """
    Fetch all Smalls events for a given date using the AJAX calendar endpoint.
    Returns a list of dicts: {title, url, time_display, venue, date}
    """
    date_str = f"{target_date.year}-{target_date.month}-{target_date.day}"
    params = {"page": 1, "venue": "all", "starting_date": date_str}

    logger.info(f"Fetching Smalls events for {target_date} ...")
    resp = requests.get(AJAX_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    data = resp.json()
    template_html = data.get("template", "")
    soup = BeautifulSoup(template_html, "html.parser")

    events = []
    for day_block in soup.select(".flex-column.day-list"):
        date_div = day_block.select_one(".title1[data-date]")
        event_date_str = date_div["data-date"] if date_div else ""

        for event_div in day_block.select(".flex-column.day-event"):
            link = event_div.select_one("a[href]")
            if not link:
                continue

            time_div = link.select_one(".text-grey.text2")
            title_div = link.select_one(".text2.day_event_title")

            events.append({
                "title": title_div.get_text(strip=True) if title_div else "",
                "url": SMALLS_BASE + link["href"],
                "time_display": time_div.get_text(strip=True) if time_div else "",
                "date": event_date_str,
            })

    logger.info(f"Found {len(events)} Smalls event(s) on {target_date}")
    return events


def _parse_end_hour(time_display: str) -> int | None:
    """
    Extract the end hour (24h) from a time display like "11:55 PM - 4:00 AM".
    Returns None if no end time is present or it can't be parsed.
    """
    if " - " not in time_display:
        return None

    end_part = time_display.split(" - ")[-1].strip()  # "4:00 AM"
    match = re.match(r"(\d+):(\d+)\s*(AM|PM)", end_part, re.IGNORECASE)
    if not match:
        return None

    hour, minute, period = int(match.group(1)), int(match.group(2)), match.group(3).upper()
    if period == "PM" and hour != 12:
        hour += 12
    elif period == "AM" and hour == 12:
        hour = 0

    return hour


def is_late_night_candidate(event: dict) -> bool:
    """True if the event's end time falls in the late-night jam window."""
    end_hour = _parse_end_hour(event.get("time_display", ""))
    return end_hour in LATE_NIGHT_END_HOURS


def fetch_event_details(event_url: str) -> dict:
    """
    Fetch an event detail page and extract:
      - description (the 'Open jam session...' text)
      - artists [{name, instrument, artist_pk}]
      - start_time, end_time strings
      - is_open_jam bool
    """
    logger.info(f"Fetching event details: {event_url}")
    resp = requests.get(event_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title_el = soup.select_one(".current-event.event-title.title1")
    title = title_el.get_text(strip=True) if title_el else ""

    # Time display: "From 11:55 PM - 4:00 AM"
    time_el = soup.select_one(".event-sets.title5")
    time_display = time_el.get_text(strip=True) if time_el else ""
    # Strip leading "From "
    time_display = re.sub(r"^From\s+", "", time_display)

    # Description: the .title2 that is NOT .event-subtitle
    description = ""
    for el in soup.select(".current-event.event-title.title2"):
        if "event-subtitle" not in el.get("class", []):
            description = el.get_text(strip=True)
            break

    # Musicians: .event-band .current_event a.artist-link
    artists = []
    for link in soup.select(".event-band .current_event a.artist-link"):
        text = link.get_text(strip=True)   # "Anthony Wonsey / Piano"
        pk_match = re.search(r"artist_pk=(\d+)", link.get("href", ""))
        if " / " in text:
            name, instrument = text.split(" / ", 1)
            artists.append({
                "name": name.strip(),
                "instrument": instrument.strip(),
                "artist_pk": pk_match.group(1) if pk_match else None,
            })

    is_open_jam = "open jam" in description.lower()

    return {
        "title": title,
        "url": event_url,
        "time_display": time_display,
        "description": description,
        "artists": artists,
        "is_open_jam": is_open_jam,
    }


def find_open_jam(target_date: date) -> dict | None:
    """
    Find tonight's open jam session on smallslive.com.
    Returns a details dict or None if not found.
    """
    events = fetch_events_for_date(target_date)
    candidates = [e for e in events if is_late_night_candidate(e)]

    if not candidates:
        logger.warning(f"No late-night events found for {target_date}")
        return None

    logger.info(f"{len(candidates)} late-night candidate(s): {[e['title'] for e in candidates]}")

    for event in candidates:
        details = fetch_event_details(event["url"])
        if details["is_open_jam"]:
            logger.info(f"Open jam confirmed: {details['title']} — {details['time_display']}")
            return {
                "session_date": str(target_date),
                "event_title": details["title"],
                "event_url": details["url"],
                "time_display": details["time_display"],
                "description": details["description"],
                "artists": details["artists"],
                "is_open_jam": True,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

    logger.warning("No open jam session found in late-night candidates")
    return None


# ── Jam history storage ───────────────────────────────────────────────────────

def save_lineup(entry: dict, history_file: Path = DEFAULT_HISTORY_FILE) -> None:
    history = _load_history(history_file)
    history[entry["session_date"]] = entry
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
    logger.info(f"Saved lineup for {entry['session_date']} to {history_file}")


def load_lineup(target_date: date, history_file: Path = DEFAULT_HISTORY_FILE) -> dict | None:
    history = _load_history(history_file)
    return history.get(str(target_date))


def _load_history(history_file: Path) -> dict:
    if history_file.exists():
        with open(history_file) as f:
            return json.load(f)
    return {}


# ── YouTube video search ──────────────────────────────────────────────────────

def find_youtube_video(
    headliner: str,
    event_date: date,
    max_age_hours: int = 36,
) -> dict | None:
    """
    Search YouTube for the Smalls recording of a specific event.
    Uses headliner name for a precise search when available.
    Returns {url, title, video_id, upload_date} or None.
    """
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("yt-dlp not installed")

    # Build targeted queries: try precise first, fall back to generic
    queries = []
    if headliner:
        queries.append(f'ytsearch5:{headliner} Smalls Jazz Club')
    queries.append("ytsearch10:Smalls Jazz Club late night jam session")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ydl_opts = {"quiet": True, "extract_flat": True, "playlistend": 10}

    for query in queries:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                entries = info.get("entries") or []

            for entry in entries:
                if not entry:
                    continue

                title = entry.get("title", "")
                video_id = entry.get("id", "")
                url = f"https://www.youtube.com/watch?v={video_id}"

                # Try to get upload date
                upload_date = _parse_yt_date(entry.get("upload_date"))

                # Check if the date roughly matches (within max_age_hours)
                if upload_date and upload_date < cutoff:
                    continue

                # Check title contains any part of the headliner or "Smalls"
                if headliner:
                    # Match first word of headliner (usually surname) as basic check
                    first_word = headliner.split()[0].lower()
                    if first_word not in title.lower() and "smalls" not in title.lower():
                        continue

                logger.info(f"Found video: '{title}' ({url})")
                return {
                    "url": url,
                    "title": title,
                    "video_id": video_id,
                    "upload_date": upload_date.isoformat() if upload_date else None,
                }
        except Exception as e:
            logger.warning(f"Query '{query}' failed: {e}")
            continue

    return None


def _parse_yt_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── Nightly runner ────────────────────────────────────────────────────────────

def nightly_run(
    today: date | None = None,
    history_file: Path = DEFAULT_HISTORY_FILE,
) -> dict:
    """
    Full nightly run. Returns a dict with:
      - video: the YouTube video to process (or None)
      - lineup: tonight's scraped lineup (or None)
      - yesterday_lineup: the stored lineup used for video search
    """
    if today is None:
        today = date.today()

    yesterday = today - timedelta(days=1)

    # ── 1. Find last night's video using stored headliner ─────────────────────
    yesterday_lineup = load_lineup(yesterday, history_file)
    headliner = yesterday_lineup["event_title"] if yesterday_lineup else ""

    if headliner:
        logger.info(f"Searching YouTube for last night's video: '{headliner}' ({yesterday})")
    else:
        logger.info(f"No stored lineup for {yesterday}, using generic search")

    video = find_youtube_video(headliner=headliner, event_date=yesterday)

    if video:
        logger.info(f"Video found: {video['url']}")
    else:
        logger.warning("No YouTube video found for last night")

    # ── 2. Scrape tonight's jam lineup ────────────────────────────────────────
    tonight_lineup = find_open_jam(today)
    if tonight_lineup:
        save_lineup(tonight_lineup, history_file)
        artists = [f"{a['name']} ({a['instrument']})" for a in tonight_lineup["artists"]]
        logger.info(f"Tonight's jam lineup: {', '.join(artists)}")
    else:
        logger.warning("Could not find tonight's open jam lineup")

    return {
        "video": video,
        "lineup": tonight_lineup,
        "yesterday_lineup": yesterday_lineup,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Smalls Live nightly scraper")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true",
                       help="Full nightly run: find last night's video + scrape tonight's lineup")
    group.add_argument("--scrape-upcoming", action="store_true",
                       help="Scrape tonight's jam lineup only")
    group.add_argument("--find-video", metavar="DATE",
                       help="Find YouTube video for a specific date (YYYY-MM-DD)")
    group.add_argument("--show", metavar="DATE",
                       help="Print stored lineup for a date (YYYY-MM-DD)")

    parser.add_argument("--date", default=None,
                        help="Override today's date for --scrape-upcoming (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="Output as JSON")
    parser.add_argument("--history-file", default=str(DEFAULT_HISTORY_FILE),
                        help="Path to jam history JSON file")

    args = parser.parse_args()
    history_file = Path(args.history_file)

    if args.run:
        result = nightly_run(history_file=history_file)
        if args.output_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if result["video"]:
                print(result["video"]["url"])
            else:
                logger.error("No video found")
                sys.exit(1)

    elif args.scrape_upcoming:
        target = date.fromisoformat(args.date) if args.date else date.today()
        lineup = find_open_jam(target)
        if not lineup:
            logger.error("No open jam found")
            sys.exit(1)
        if args.output_json:
            print(json.dumps(lineup, indent=2, default=str))
        else:
            print(f"{lineup['event_title']} — {lineup['time_display']}")
            for a in lineup["artists"]:
                print(f"  {a['name']} / {a['instrument']}")
        save_lineup(lineup, history_file)

    elif args.find_video:
        target = date.fromisoformat(args.find_video)
        lineup = load_lineup(target, history_file)
        headliner = lineup["event_title"] if lineup else ""
        video = find_youtube_video(headliner=headliner, event_date=target)
        if not video:
            logger.error("No video found")
            sys.exit(1)
        if args.output_json:
            print(json.dumps(video, indent=2, default=str))
        else:
            print(video["url"])

    elif args.show:
        lineup = load_lineup(date.fromisoformat(args.show), history_file)
        if not lineup:
            print(f"No lineup stored for {args.show}")
            sys.exit(1)
        print(json.dumps(lineup, indent=2, default=str))


if __name__ == "__main__":
    main()
