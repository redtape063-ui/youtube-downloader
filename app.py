import os
import sys
import uuid
import time
import shutil
import threading
import subprocess
import zipfile
import io
import json
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, after_this_request
from werkzeug.utils import secure_filename

# Ensure ffmpeg and ffprobe are in PATH
try:
    ffmpeg_dir = "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/static_ffmpeg/bin/darwin_arm64"
    if os.path.exists(ffmpeg_dir) and ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

import yt_dlp
from converter import convert_document, is_allowed_file

app = Flask(__name__)

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
UPLOAD_FOLDER = BASE_DIR / "uploads"
CONVERTED_FOLDER = BASE_DIR / "converted"

DOWNLOADS_DIR.mkdir(exist_ok=True)
UPLOAD_FOLDER.mkdir(exist_ok=True)
CONVERTED_FOLDER.mkdir(exist_ok=True)

app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['CONVERTED_FOLDER'] = str(CONVERTED_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# In-memory task registry for YouTube downloads
tasks = {}
tasks_lock = threading.Lock()

def cleanup_old_files():
    """Periodically clean up downloaded files and converted docs older than 30 minutes."""
    while True:
        try:
            time.sleep(300)
            now = time.time()
            for directory in [DOWNLOADS_DIR, UPLOAD_FOLDER, CONVERTED_FOLDER]:
                for item in directory.glob("**/*"):
                    if item.is_file() and now - item.stat().st_mtime > 1800:
                        try:
                            item.unlink()
                        except Exception:
                            pass
            # remove empty directories in downloads
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
    """Ensure MP4 video uses standard H.264 & AAC for 100% universal compatibility."""
    filepath = Path(filepath)
    if not filepath.exists() or filepath.suffix.lower() != '.mp4':
        return filepath

    vcodec, acodec = get_media_codecs(filepath)
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
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"])
    else:
        cmd.extend(["-c:v", "copy"])

    if needs_atranscode:
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


# ==========================================================
# 1. 유튜브 다운로더 API
# ==========================================================
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

    # Check for cookie file
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

    # Check for cookie file
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
        h = quality if (quality and str(quality).isdigit()) else 1080
        ydl_opts['format'] = (
            f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio/"
            f"bestvideo[height<={h}]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/best"
        )
        ydl_opts['merge_output_format'] = 'mp4'
        ydl_opts['postprocessor_args'] = {
            'merger': ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k']
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        downloaded_files = [f for f in task_dir.iterdir() if f.is_file() and not f.name.endswith('.part')]
        if not downloaded_files:
            raise Exception("다운로드된 파일을 찾을 수 없습니다.")

        final_file = downloaded_files[0]

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
    format_type = data.get('format', 'video')
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


# ==========================================================
# 2. 한글(HWP / HWPX) -> PDF 변환기 API
# ==========================================================
def get_meta_path(file_id):
    return os.path.join(app.config['CONVERTED_FOLDER'], f"{file_id}.json")

def save_record(file_id, record):
    meta_path = get_meta_path(file_id)
    try:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False)
    except Exception as e:
        app.logger.error(f"Error saving record: {e}")

def get_record(file_id):
    meta_path = get_meta_path(file_id)
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    pdf_path = os.path.join(app.config['CONVERTED_FOLDER'], f"{file_id}.pdf")
    if os.path.exists(pdf_path):
        return {
            'id': file_id,
            'pdf_path': pdf_path,
            'display_name': 'document.pdf'
        }
    return None


@app.route('/api/convert', methods=['POST'])
def convert():
    if 'files' not in request.files:
        return jsonify({'error': '업로드할 파일이 없습니다.'}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'error': '선택된 파일이 없습니다.'}), 400

    results = []
    for file in files:
        original_name = file.filename
        if not is_allowed_file(original_name):
            results.append({
                'original_name': original_name,
                'success': False,
                'error': '지원하지 않는 확장자입니다. (.hwp, .hwpx 파일만 가능)'
            })
            continue

        file_id = str(uuid.uuid4())
        ext = os.path.splitext(original_name)[1].lower()
        saved_name = f"{file_id}{ext}"
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_name)
        
        pdf_name = f"{os.path.splitext(original_name)[0]}.pdf"
        output_pdf_filename = f"{file_id}.pdf"
        output_pdf_path = os.path.join(app.config['CONVERTED_FOLDER'], output_pdf_filename)

        file.save(input_path)
        original_size = os.path.getsize(input_path)

        success, elapsed, error = convert_document(input_path, output_pdf_path)

        if success and os.path.exists(output_pdf_path):
            pdf_size = os.path.getsize(output_pdf_path)
            record = {
                'id': file_id,
                'original_name': original_name,
                'display_name': pdf_name,
                'original_size': original_size,
                'pdf_size': pdf_size,
                'elapsed': elapsed,
                'pdf_path': output_pdf_path
            }
            save_record(file_id, record)
            results.append({
                'id': file_id,
                'original_name': original_name,
                'pdf_name': pdf_name,
                'original_size': original_size,
                'pdf_size': pdf_size,
                'elapsed': elapsed,
                'success': True
            })
        else:
            results.append({
                'original_name': original_name,
                'success': False,
                'elapsed': elapsed,
                'error': error or '변환 실패'
            })

    return jsonify({'results': results})


@app.route('/api/preview/<file_id>')
def preview_pdf(file_id):
    record = get_record(file_id)
    if not record or not os.path.exists(record['pdf_path']):
        return "파일을 찾을 수 없습니다.", 404
    return send_file(
        record['pdf_path'],
        mimetype='application/pdf',
        as_attachment=False,
        download_name=record['display_name']
    )


@app.route('/api/hwp-download/<file_id>')
def download_pdf(file_id):
    record = get_record(file_id)
    if not record or not os.path.exists(record['pdf_path']):
        return "파일을 찾을 수 없습니다.", 404
    return send_file(
        record['pdf_path'],
        mimetype='application/pdf',
        as_attachment=True,
        download_name=record['display_name']
    )


@app.route('/api/download-all', methods=['GET'])
def download_all_pdfs():
    ids_param = request.args.get('ids', '')
    ids = [i.strip() for i in ids_param.split(',') if i.strip()]
    
    if not ids:
        return "다운로드할 파일 ID가 없습니다.", 400

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_id in ids:
            record = get_record(file_id)
            if record and os.path.exists(record['pdf_path']):
                zip_file.write(record['pdf_path'], arcname=record['display_name'])

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='converted_pdfs.zip'
    )


@app.route('/api/sample')
def get_sample_hwp():
    sample_path = BASE_DIR / '한글_HWPX_샘플문서.hwpx'
    if not sample_path.exists():
        from create_sample import create_sample_hwpx
        create_sample_hwpx(str(sample_path))
    return send_file(
        str(sample_path),
        as_attachment=True,
        download_name='한글_HWPX_샘플문서.hwpx',
        mimetype='application/hwp+zip'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"Starting Integrated Toolbox on http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
