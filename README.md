# Tubegrab: YouTube Downloader (FastAPI + yt-dlp + FFmpeg)

Use it only for content you own, that is licensed for download, or where downloading is permitted. Downloading may violate YouTube's Terms of Service.

## 1. Install FFmpeg
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: `winget install Gyan.FFmpeg` (or download from ffmpeg.org and add `bin` to PATH)

Verify: `ffmpeg -version`

## 2. Set up Python environment
```bash
cd yt-downloader
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # fastapi, uvicorn, yt-dlp
```

## 3. Run
```bash
python main.py
```
Open http://127.0.0.1:8000. The server prints `FFmpeg: <path>` on startup, and the UI shows a red banner if it is missing. You can also check `GET /api/health`.

## Install as an app
- Open the site in Chrome and use the Install app prompt or Chrome's install control.
- On iPhone or iPad, open the site in Safari, tap Share, then choose **Add to Home Screen**.
- The application shell and icons are cached for offline launch. Video lookup and downloads still require the app server and an internet connection.
- Service workers and PWA installation require a secure context: use HTTPS in production. Browsers treat `localhost` as secure for local development.
- Bump `CACHE_VERSION` in `static/sw.js` when changing the cache strategy or shell; activation removes older Tubegrab caches automatically.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/info` | `{url}` returns title, thumbnail, channel, duration, `video` and `audio` format lists |
| POST | `/api/thumbnail` | `{url}` downloads the video's thumbnail image |
| POST | `/api/download` | `{url, format}` (`"1080"`, `"2160"`, `"audio-best"`, `"mp3-320"`...) returns `{job_id}` |
| GET | `/api/progress/{job_id}` | SSE stream: percent, speed, ETA, status |
| GET | `/api/file/{job_id}` | Serves the file, then deletes it |

## Notes
- Files live in `downloads/`; they are deleted after being served, and anything older than 30 minutes is purged every minute. The folder is wiped on startup.
- YouTube changes often. If extraction fails, run `pip install -U yt-dlp`.
- Private and age-restricted videos show clear error banners (no cookies are used).
