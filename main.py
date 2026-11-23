"""YouTube Downloader: FastAPI + yt-dlp + FFmpeg."""
import asyncio, json, re, shutil, threading, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.request import urlopen

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

BASE = Path(__file__).parent
DOWNLOADS = BASE / "downloads"
MAX_AGE = 30 * 60  # seconds before orphaned files are purged
YT_RE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)
TIERS = [(2160, "4K - 2160p"), (1440, "2K - 1440p"), (1080, "Full HD - 1080p"),
         (720, "HD - 720p"), (480, "SD - 480p"), (360, "360p")]
AUDIO_OPTS = {  # id: (codec, quality)
    "audio-best": ("m4a", "256"),
    "mp3-320": ("mp3", "320"),
    "mp3-192": ("mp3", "192"),
    "mp3-128": ("mp3", "128"),
}
AUDIO_FORMATS = [
    {"id": "audio-best", "label": "Best Quality - M4A (original, no re-encode)"},
    {"id": "mp3-320", "label": "MP3 - 320 kbps"},
    {"id": "mp3-192", "label": "MP3 - 192 kbps"},
    {"id": "mp3-128", "label": "MP3 - 128 kbps"},
]
PUBLIC = ("state", "percent", "speed", "eta", "status", "error", "filename")
jobs: dict[str, dict] = {}


def ffmpeg_path():
    return shutil.which("ffmpeg")


def friendly(err) -> str:
    m = str(err).lower()
    if "private video" in m:
        return "This video is private. Only its owner can access it."
    if "confirm your age" in m or "age-restricted" in m or "age restricted" in m:
        return "This video is age-restricted and can’t be downloaded without signing in."
    if "not a valid url" in m or "unsupported url" in m:
        return "That doesn’t look like a valid YouTube link."
    if "sign in to confirm you" in m:
        return "YouTube is asking for verification. Update yt-dlp (pip install -U yt-dlp) and try again."
    if "unavailable" in m or "removed" in m or "terminated" in m:
        return "This video is unavailable. It may be deleted or blocked in your region."
    if "ffmpeg" in m:
        return "FFmpeg was not found. Install it and restart the server."
    return "Something went wrong: " + re.sub(r"\x1b\[[0-9;]*m|ERROR: ", "", str(err)).strip()[:200]


def fmt_duration(s):
    s = int(s or 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def cleanup_loop():
    while True:
        now = time.time()
        for f in DOWNLOADS.glob("*"):
            try:
                if f.is_file() and now - f.stat().st_mtime > MAX_AGE:
                    f.unlink()
            except OSError:
                pass
        for jid in [j for j, v in jobs.items() if now - v["created"] > MAX_AGE]:
            jobs.pop(jid, None)
        time.sleep(60)


@asynccontextmanager
async def lifespan(_):
    DOWNLOADS.mkdir(exist_ok=True)
    for f in DOWNLOADS.glob("*"):  # wipe leftovers from previous runs
        if f.is_file():
            f.unlink()
    threading.Thread(target=cleanup_loop, daemon=True).start()
    print("FFmpeg:", ffmpeg_path() or "NOT FOUND - merging and MP3 conversion will fail")
    yield


app = FastAPI(title="YouTube Downloader", lifespan=lifespan)


class InfoReq(BaseModel):
    url: str


class DownloadReq(BaseModel):
    url: str
    format: str  # "2160", "1080", ... or "mp3"


class ThumbnailReq(BaseModel):
    url: str


@app.get("/api/health")
def health():
    return {"ffmpeg": bool(ffmpeg_path())}


def _extract(url):
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        return ydl.extract_info(url, download=False)


@app.post("/api/info")
async def info(req: InfoReq):
    url = req.url.strip()
    if not YT_RE.match(url):
        raise HTTPException(400, "That doesn’t look like a valid YouTube link.")
    try:
        data = await asyncio.to_thread(_extract, url)
    except Exception as e:
        raise HTTPException(400, friendly(e))
    heights = {f["height"] for f in data.get("formats", [])
               if f.get("height") and f.get("vcodec") not in (None, "none")}
    top = max(heights, default=720)
    formats = [{"id": str(h), "label": label} for h, label in TIERS if h <= top]
    if formats:
        formats[0]["label"] += " (Best Quality)"
    else:
        formats = [{"id": "360", "label": "360p"}]
    return {"title": data.get("title"), "thumbnail": data.get("thumbnail"),
            "channel": data.get("channel") or data.get("uploader"),
            "duration": fmt_duration(data.get("duration")), "video": formats, "audio": AUDIO_FORMATS}


def run_job(job_id, url, fmt):
    job = jobs[job_id]
    audio = fmt in AUDIO_OPTS
    expected = 1 if audio else 2  # video + audio streams
    done = {"n": 0}

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes") or 0) / total if total else 0
            job["percent"] = round(min(((done["n"] + frac) / expected) * 95, 95), 1)
            sp = d.get("speed")
            job["speed"] = f"{sp / 1048576:.1f} MB/s" if sp else ""
            job["eta"] = f"{int(d['eta'])}s" if d.get("eta") is not None else ""
            job["status"] = ("Downloading audio..." if audio or done["n"] >= 1
                             else "Downloading video...")
        elif d["status"] == "finished":
            done["n"] += 1

    def pp_hook(d):
        if d["status"] == "started":
            job["speed"] = job["eta"] = ""
            job["percent"] = 97
            job["status"] = ("Converting audio with FFmpeg..." if d["postprocessor"] == "ExtractAudio"
                             else "Merging audio with FFmpeg...")

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "outtmpl": str(DOWNLOADS / f"{job_id}.%(ext)s"),
            "progress_hooks": [hook], "postprocessor_hooks": [pp_hook]}
    if audio:
        codec, quality = AUDIO_OPTS[fmt]
        opts.update(format="bestaudio[ext=m4a]/bestaudio/best" if codec == "m4a" else "bestaudio/best",
                    postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": codec,
                                     "preferredquality": quality}])
    else:
        h = int(fmt)
        opts.update(format=f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
                    merge_output_format="mp4")
    try:
        if not ffmpeg_path():
            raise RuntimeError("FFmpeg not found")
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=True)
        ext = AUDIO_OPTS[fmt][0] if audio else "mp4"
        path = DOWNLOADS / f"{job_id}.{ext}"
        if not path.exists():
            raise RuntimeError("Output file missing after download")
        safe = re.sub(r'[\\/:*?"<>|]+', "", data.get("title") or "video").strip()[:120] or "video"
        job.update(path=str(path), filename=f"{safe}.{ext}", percent=100, speed="", eta="",
                   status="Ready!", state="done")
    except Exception as e:
        job.update(state="error", error=friendly(e), status="Failed")


@app.post("/api/download")
async def download(req: DownloadReq):
    url = req.url.strip()
    if not YT_RE.match(url):
        raise HTTPException(400, "That doesn’t look like a valid YouTube link.")
    if req.format not in AUDIO_OPTS and not req.format.isdigit():
        raise HTTPException(400, "Unknown format.")
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"state": "running", "percent": 0, "speed": "", "eta": "", "status": "Starting...",
                    "error": None, "filename": None, "path": None, "created": time.time()}
    threading.Thread(target=run_job, args=(job_id, url, req.format), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/thumbnail")
async def download_thumbnail(req: ThumbnailReq):
    url = req.url.strip()
    if not YT_RE.match(url):
        raise HTTPException(400, "That doesn’t look like a valid YouTube link.")
    try:
        data = await asyncio.to_thread(_extract, url)
        thumbnail_url = data.get("thumbnail")
        if not thumbnail_url:
            raise RuntimeError("Thumbnail not available")

        def save_thumbnail():
            with urlopen(thumbnail_url, timeout=20) as response:
                content_type = response.headers.get_content_type()
                image = response.read()
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")
            job_id = uuid.uuid4().hex
            path = DOWNLOADS / f"{job_id}{extension}"
            path.write_bytes(image)
            return job_id, path

        job_id, path = await asyncio.to_thread(save_thumbnail)
        safe = re.sub(r'[\\/:*?"<>|]+', "", data.get("title") or "thumbnail").strip()[:120] or "thumbnail"

        def cleanup():
            path.unlink(missing_ok=True)

        return FileResponse(path, filename=f"{safe}-thumbnail{path.suffix}",
                            background=BackgroundTask(cleanup),
                            media_type={".jpg": "image/jpeg", ".png": "image/png",
                                        ".webp": "image/webp"}[path.suffix])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, friendly(e))


@app.get("/api/progress/{job_id}")
async def progress(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job.")

    async def stream():
        last = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield 'data: {"state":"error","error":"Job expired."}\n\n'
                return
            payload = json.dumps({k: job[k] for k in PUBLIC})
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if job["state"] in ("done", "error"):
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/file/{job_id}")
def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job["state"] != "done" or not Path(job["path"]).exists():
        raise HTTPException(404, "File not found or already downloaded.")
    path = Path(job["path"])

    def cleanup():
        path.unlink(missing_ok=True)
        jobs.pop(job_id, None)

    return FileResponse(path, filename=job["filename"], background=BackgroundTask(cleanup),
                        media_type={".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(path.suffix, "video/mp4"))


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
