import os
import sys
import uuid
import time
import shutil
import threading
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, after_this_request

# Ensure ffmpeg and ffprobe are in PATH
try:
    ffmpeg_dir = "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/static_ffmpeg/bin/darwin_arm64"
    if os.path.exists(ffmpeg_dir) and ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception as e:
    pass

import yt_dlp

app = Flask(__name__)

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# In-memory task registry
tasks = {}
tasks_lock = threading.Lock()

def cleanup_old_files():
    """Periodically clean up downloaded files older than 30 minutes."""
    while True:
        try:
            time.sleep(300)
            now = time.time()
            for item in DOWNLOADS_DIR.glob("**/*"):
                if item.is_file() and now - item.stat().st_mtime > 1800:
                    try:
                        item.unlink()
                    except Exception:
                        pass
            # remove empty directories
            for item in DOWNLOADS_DIR.iterdir():
                if item.is_dir() and not any(item.iterdir()):
                    try:
                        item.rmdir()
                    except Exception:
                        pass
        except Exception:
            pass

cleaner_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleaner_thread.start()


def format_seconds(seconds):
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_bytes(size):
    if not size:
        return ""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def get_media_codecs(filepath):
    """Inspect actual video and audio codecs inside a media file using ffprobe."""
    try:
        ffprobe_bin = shutil.which("ffprobe") or "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/static_ffmpeg/bin/darwin_arm64/ffprobe"
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "csv=p=0",
            str(filepath)
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().splitlines()
        vcodec, acodec = None, None
        for line in output:
            parts = line.strip().split(',')
            if len(parts) == 2:
                ctype, cname = parts[0].strip().lower(), parts[1].strip().lower()
                if ctype == 'video' and not vcodec:
                    vcodec = cname
                elif ctype == 'audio' and not acodec:
                    acodec = cname
        return vcodec, acodec
    except Exception as e:
        print(f"Error checking codecs: {e}")
        return None, None


def ensure_h264_aac_compatibility(filepath, task_id):
    """
    Ensure the downloaded MP4 video file uses universal H.264 (yuv420p) and AAC codecs
    so it plays without any codec error on Windows Media Player, QuickTime, iOS, TV, etc.
    """
    filepath = Path(filepath)
    if not filepath.exists() or filepath.suffix.lower() != '.mp4':
        return filepath

    vcodec, acodec = get_media_codecs(filepath)
    # H.264 codecs: h264, avc1
    # AAC codecs: aac, mp4a
    needs_vtranscode = vcodec and vcodec not in ['h264', 'avc1']
    needs_atranscode = acodec and acodec not in ['aac', 'mp4a']

    if not needs_vtranscode and not needs_atranscode:
        return filepath

    with tasks_lock:
        if task_id in tasks:
            tasks[task_id].update({
                'status': 'processing',
                'progress': 99,
                'eta': '모든 플레이어 호환을 위해 H.264/AAC 코덱으로 최적화 중...',
            })

    ffmpeg_bin = shutil.which("ffmpeg") or "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/static_ffmpeg/bin/darwin_arm64/ffmpeg"
    temp_output = filepath.with_name(f"fixed_{filepath.name}")

    cmd = [ffmpeg_bin, "-y", "-i", str(filepath)]
    if needs_vtranscode:
        # Transcode VP9 or AV1 to standard H.264 (yuv420p 8-bit for 100% device compatibility)
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"])
    else:
        cmd.extend(["-c:v", "copy"])

    if needs_atranscode:
        # Transcode Opus or other audio to AAC
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-c:a", "copy"])

    cmd.append(str(temp_output))

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if temp_output.exists() and temp_output.stat().st_size > 0:
            filepath.unlink()
            temp_output.rename(filepath)
    except Exception as e:
        print(f"Codec normalization fallback: {e}")
        if temp_output.exists():
            temp_output.unlink()

    return filepath


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def get_video_info():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': '유튜브 URL을 입력해주세요.'}), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
    }

    # Check for cookie file (Render Secret Files / local)
    for cpath in ["cookies.txt", "/etc/secrets/cookies.txt", BASE_DIR / "cookies.txt"]:
        if os.path.exists(cpath):
            ydl_opts['cookiefile'] = str(cpath)
            break

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = info.get('formats', [])
            resolutions = set()
            for f in formats:
                h = f.get('height')
                vcodec = f.get('vcodec')
                if h and vcodec and vcodec != 'none':
                    resolutions.add(h)
            
            sorted_res = sorted(list(resolutions), reverse=True)
            
            res_options = []
            for r in [2160, 1440, 1080, 720, 480, 360]:
                if any(res >= r for res in sorted_res):
                    label = f"{r}p"
                    badge = ""
                    if r == 1080:
                        label = "1080p FHD"
                        badge = "추천 (H.264)"
                    elif r == 720:
                        label = "720p HD"
                        badge = "H.264"
                    elif r >= 2160:
                        label = "4K (2160p)"
                        badge = "초고화질"
                    elif r >= 1440:
                        label = "QHD (1440p)"
                        badge = "초고화질"
                    res_options.append({'height': r, 'label': label, 'badge': badge})
            
            if not res_options and sorted_res:
                res_options.append({'height': sorted_res[0], 'label': f"{sorted_res[0]}p", 'badge': ''})

            result = {
                'id': info.get('id'),
                'title': info.get('title'),
                'uploader': info.get('uploader') or info.get('channel') or '알 수 없음',
                'duration': format_seconds(info.get('duration')),
                'thumbnail': info.get('thumbnail'),
                'view_count': f"{info.get('view_count', 0):,}" if info.get('view_count') is not None else "-",
                'available_resolutions': res_options
            }
            return jsonify(result)
    except Exception as e:
        error_msg = str(e)
        if "Unsupported URL" in error_msg:
            return jsonify({'error': '유효하지 않은 유튜브 URL입니다.'}), 400
        elif "Private video" in error_msg:
            return jsonify({'error': '비공개 동영상입니다.'}), 400
        elif "Sign in to confirm your age" in error_msg:
            return jsonify({'error': '연령 제한 동영상입니다.'}), 400
        return jsonify({'error': f"정보를 가져오는데 실패했습니다: {error_msg}"}), 500


def run_download_task(task_id, url, format_type, quality):
    task_dir = DOWNLOADS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(task_dir / "%(title).200B.%(ext)s")

    def progress_hook(d):
        with tasks_lock:
            if task_id not in tasks:
                return
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                progress = round((downloaded / total * 100), 1) if total > 0 else 0
                speed = format_bytes(d.get('speed', 0)) + "/s" if d.get('speed') else ""
                eta_sec = d.get('eta')
                eta = f"{eta_sec}초 남음" if eta_sec is not None else ""
                
                tasks[task_id].update({
                    'status': 'downloading',
                    'progress': progress,
                    'speed': speed,
                    'eta': eta,
                })
            elif d['status'] == 'finished':
                tasks[task_id].update({
                    'status': 'processing',
                    'progress': 99,
                    'speed': '',
                    'eta': '오디오 및 비디오 처리 중...',
                })

    ydl_opts = {
        'outtmpl': outtmpl,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'windowsfilenames': True,
    }

    # Check for cookie file (Render Secret Files / local)
    for cpath in ["cookies.txt", "/etc/secrets/cookies.txt", BASE_DIR / "cookies.txt"]:
        if os.path.exists(cpath):
            ydl_opts['cookiefile'] = str(cpath)
            break

    if format_type == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        # Strictly prioritize H.264 (avc1) and AAC (mp4a) for universal compatibility
        # This prevents yt-dlp from picking AV1 (av01) or VP9 which cause "codec unsupported" errors on Windows Media Player & QuickTime
        h = quality if (quality and str(quality).isdigit()) else 1080
        ydl_opts['format'] = (
            f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio/"
            f"bestvideo[height<={h}]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/best"
        )
        ydl_opts['merge_output_format'] = 'mp4'
        # Ensure that if audio stream was Opus, merger transcodes it to standard AAC
        ydl_opts['postprocessor_args'] = {
            'merger': ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k']
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Locate downloaded file
        downloaded_files = [f for f in task_dir.iterdir() if f.is_file() and not f.name.endswith('.part')]
        if not downloaded_files:
            raise Exception("다운로드된 파일을 찾을 수 없습니다.")

        final_file = downloaded_files[0]

        # Normalize video/audio codecs if format is MP4 video
        if format_type != 'audio' and final_file.suffix.lower() == '.mp4':
            final_file = ensure_h264_aac_compatibility(final_file, task_id)

        with tasks_lock:
            tasks[task_id].update({
                'status': 'completed',
                'progress': 100,
                'filename': final_file.name,
                'filepath': str(final_file),
                'eta': '다운로드 준비 완료!',
            })
    except Exception as e:
        with tasks_lock:
            tasks[task_id].update({
                'status': 'error',
                'error': str(e),
            })


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_type = data.get('format', 'video')  # 'video' or 'audio'
    quality = data.get('quality', 1080)

    if not url:
        return jsonify({'error': '유튜브 URL이 필요합니다.'}), 400

    task_id = str(uuid.uuid4())
    with tasks_lock:
        tasks[task_id] = {
            'status': 'init',
            'progress': 0,
            'speed': '',
            'eta': '다운로드 준비 중...',
            'filename': '',
            'filepath': '',
            'error': None,
            'created_at': time.time(),
        }

    thread = threading.Thread(
        target=run_download_task,
        args=(task_id, url, format_type, quality),
        daemon=True
    )
    thread.start()

    return jsonify({'task_id': task_id})


@app.route('/api/progress/<task_id>')
def get_progress(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return jsonify({'error': '존재하지 않는 작업입니다.'}), 404
        return jsonify(task)


@app.route('/api/file/<task_id>')
def download_file(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task or task.get('status') != 'completed':
            return jsonify({'error': '다운로드가 완료되지 않았거나 찾을 수 없습니다.'}), 404
        filepath = task.get('filepath')
        filename = task.get('filename')

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': '파일이 서버에 존재하지 않습니다.'}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
        conditional=True
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"Starting YouTube Downloader on http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
