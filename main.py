from __future__ import annotations
import argparse
import os
import sys
import shutil
import threading
import time
from typing import Optional, Dict, Any
import logging
import math

try:
    import yt_dlp as ytdlp
except Exception as e:
    print("ERROR: yt-dlp is required. Install with: pip install -U \"yt-dlp[default]\"")
    raise

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from PIL import Image, ImageTk
    GUI_AVAILABLE = True
except Exception:
    GUI_AVAILABLE = False

DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
MAX_RETRIES = 3
LOG_FORMAT = "%(asctime)s — %(levelname)s — %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("ytdl_app")

def check_ffmpeg_available() -> bool:
    """Return True if ffmpeg executable is available on PATH."""
    return shutil.which("ffmpeg") is not None

def safe_output_template(output_dir: str = DEFAULT_OUTPUT_DIR) -> str:
    """Return a safe yt-dlp output template inside output_dir."""
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, "%(title)s [%(id)s].%(ext)s")

def human_bytes(n: int) -> str:
    """Pretty-print bytes."""
    if n < 0:
        return "Unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units)-1:
        n /= 1024.0
        i += 1
    return f"{n:.2f} {units[i]}"

def add_context_menu(widget: tk.Widget):
    """Attach a right-click context menu (cut/copy/paste/select all) to a widget."""
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Select All", command=lambda: widget.event_generate("<<SelectAll>>"))

    def show_menu(event):
        menu.tk_popup(event.x_root, event.y_root)
    widget.bind("<Button-3>", show_menu)

class YTDLDownloader:
    def __init__(self, output_dir: str = DEFAULT_OUTPUT_DIR, verbose: bool = False):
        self.output_dir = output_dir
        self.verbose = verbose
        self._last_progress: Dict[str, Any] = {}
        self._stop_requested = False

    def _progress_hook(self, d: Dict[str, Any]):
        status = d.get("status")
        if status == "downloading":
            downloaded_bytes = d.get("downloaded_bytes", -1)
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate", -1)
            speed = d.get("speed")
            eta = d.get("eta")
            percent = None
            if total_bytes and downloaded_bytes and total_bytes > 0:
                percent = downloaded_bytes / total_bytes * 100
            self._last_progress = {
                "status": "downloading",
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "percent": percent,
                "speed": speed,
                "eta": eta
            }
            logger.info(f"Downloading: {percent:.2f}% ({human_bytes(downloaded_bytes)} / {human_bytes(total_bytes)}) ETA {eta}s" if percent is not None else f"Downloading: {human_bytes(downloaded_bytes)}")
        elif status == "finished":
            logger.info("Download finished; post-processing...")
            self._last_progress = {"status": "finished"}
        elif status == "error":
            logger.error("Download error reported by yt-dlp")
            self._last_progress = {"status": "error"}

    def download(self, url: str, output_format: str = "mp4", max_retries: int = MAX_RETRIES) -> str:
        if output_format not in ("mp4", "mp3"):
            raise ValueError("output_format must be 'mp4' or 'mp3'")

        needs_ffmpeg = (output_format == "mp3")
        if needs_ffmpeg and not check_ffmpeg_available():
            raise RuntimeError("FFmpeg is required for MP3 conversion but was not found on PATH. Install ffmpeg and try again.")

        outtmpl = safe_output_template(self.output_dir)
        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "quiet": False if self.verbose else True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [self._progress_hook],
            "format": "bestvideo+bestaudio/best",
            "retries": max_retries,
            "add_metadata": True,
            "merge_output_format": "mp4",
            "writethumbnail": False,
        }

        if output_format == "mp3":
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }, {
                    "key": "FFmpegMetadata"
                }],
            })
        else:
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        last_err = None
        for attempt in range(1, max_retries + 1):
            if self._stop_requested:
                raise RuntimeError("Download stopped by user.")
            try:
                logger.info(f"Starting download attempt {attempt} for {url}")
                with ytdlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    ext = "mp3" if output_format == "mp3" else "mp4"
                    title = info.get("title") or info.get("id")
                    video_id = info.get("id")
                    filename = f"{title} [{video_id}].{ext}"
                    final_path = os.path.join(self.output_dir, filename)
                    logger.info(f"Finished -> {final_path}")
                    return final_path
            except Exception as e:
                last_err = e
                logger.warning(f"Attempt {attempt} failed: {e}")
                time.sleep(1 + attempt)
                continue

        raise RuntimeError(f"All attempts failed. Last error: {last_err}")

    def stop(self):
        self._stop_requested = True

def cli_main():
    parser = argparse.ArgumentParser(prog="ytdl_app", description="Download YouTube/TikTok -> MP4 or MP3")
    parser.add_argument("--url", "-u", required=False, help="Video URL (YouTube, TikTok, etc.)")
    parser.add_argument("--format", "-f", choices=["mp4", "mp3"], default="mp4", help="Desired output format")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--gui", action="store_true", help="Open GUI")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    if args.gui:
        if not GUI_AVAILABLE:
            print("GUI components unavailable (tkinter/Pillow missing). Install them or run in CLI mode.")
            sys.exit(1)
        run_gui(default_output=args.output)
        return

    if not args.url:
        parser.print_help()
        return

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    downloader = YTDLDownloader(output_dir=args.output, verbose=args.verbose)
    try:
        path = downloader.download(args.url, output_format=args.format)
        print(f"Saved: {path}")
    except Exception as e:
        logger.exception("Download failed:")
        print("ERROR:", str(e))

def run_gui(default_output: Optional[str] = None):
    if not GUI_AVAILABLE:
        raise RuntimeError("GUI libraries (tkinter/pillow) not available")

    root = tk.Tk()
    root.title("YT/TikTok -> MP3/MP4 Downloader")
    root.geometry("640x300")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)

    url_var = tk.StringVar()
    fmt_var = tk.StringVar(value="mp4")
    out_var = tk.StringVar(value=default_output or DEFAULT_OUTPUT_DIR)
    status_var = tk.StringVar(value="Idle")

    ttk.Label(frame, text="Video URL:").grid(row=0, column=0, sticky="w")
    url_entry = ttk.Entry(frame, textvariable=url_var, width=60)
    url_entry.grid(row=0, column=1, columnspan=3, sticky="w")
    add_context_menu(url_entry)

    ttk.Label(frame, text="Format:").grid(row=1, column=0, sticky="w")
    fmt_combo = ttk.Combobox(frame, textvariable=fmt_var, values=["mp4", "mp3"], width=8, state="readonly")
    fmt_combo.grid(row=1, column=1, sticky="w")

    ttk.Label(frame, text="Output folder:").grid(row=2, column=0, sticky="w")
    out_entry = ttk.Entry(frame, textvariable=out_var, width=48)
    out_entry.grid(row=2, column=1, sticky="w")
    add_context_menu(out_entry)

    def choose_dir():
        d = filedialog.askdirectory(initialdir=out_var.get() or os.path.expanduser("~"))
        if d:
            out_var.set(d)
    ttk.Button(frame, text="Browse...", command=choose_dir).grid(row=2, column=2, sticky="w")

    progress = ttk.Progressbar(frame, orient="horizontal", length=520, mode="determinate")
    progress.grid(row=4, column=0, columnspan=4, pady=12)

    status_label = ttk.Label(frame, textvariable=status_var)
    status_label.grid(row=5, column=0, columnspan=4, sticky="w")

    downloader = YTDLDownloader(output_dir=out_var.get())
    download_thread: Optional[threading.Thread] = None

    def set_status(s: str):
        status_var.set(s)

    def do_download():
        nonlocal download_thread
        url = url_var.get().strip()
        fmt = fmt_var.get()
        outd = out_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please paste a YouTube or TikTok URL.")
            return
        if not outd:
            messagebox.showwarning("Missing output folder", "Please choose an output folder.")
            return
        downloader.output_dir = outd

        def target():
            try:
                set_status("Starting download...")
                path = downloader.download(url, output_format=fmt)
                set_status(f"Saved: {path}")
                progress["value"] = 100
                messagebox.showinfo("Done", f"Saved: {path}")
            except Exception as e:
                set_status("Error: " + str(e))
                logger.exception("Download error")
                messagebox.showerror("Download failed", str(e))

        download_thread = threading.Thread(target=target, daemon=True)
        download_thread.start()

        def monitor():
            st = downloader._last_progress
            if st.get("status") == "downloading":
                pct = st.get("percent") or 0.0
                progress["value"] = max(0, min(100, pct))
                set_status(f"Downloading: {pct:.1f}% ETA {st.get('eta')}")
            elif st.get("status") == "finished":
                progress["value"] = 95
                set_status("Post-processing...")
            root.after(500, monitor)
        monitor()

    ttk.Button(frame, text="Download", command=do_download).grid(row=3, column=1, sticky="w", pady=8)
    ttk.Button(frame, text="Quit", command=root.destroy).grid(row=3, column=2, sticky="w", pady=8)

    root.mainloop()

if __name__ == "__main__":
    if GUI_AVAILABLE:
        run_gui(default_output=DEFAULT_OUTPUT_DIR)
    else:
        print("ERROR: GUI not available (tkinter or Pillow missing). Install them via:")
        print("  pip install pillow")
        print("And ensure tkinter is installed (usually included with Python).")
