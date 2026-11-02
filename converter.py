import os
import time
import zipfile
import olefile
from hwpx_converter import convert_hwpx_to_pdf
from hwp_converter import convert_hwp_to_pdf

ALLOWED_EXTENSIONS = {'.hwp', '.hwpx'}

def is_allowed_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS

def detect_document_type(input_path):
    """
    Detect whether a document is an HWPX (ZIP-based) or standard HWP (OLE2 binary)
    by inspecting file magic bytes, regardless of file extension.
    """
    try:
        if zipfile.is_zipfile(input_path):
            return 'hwpx'
    except Exception:
        pass

    try:
        if olefile.isOleFile(input_path):
            return 'hwp'
    except Exception:
        pass

    # Fallback to inspecting raw header bytes
    try:
        with open(input_path, 'rb') as f:
            header = f.read(8)
            if header.startswith(b'PK\x03\x04'):
                return 'hwpx'
            elif header.startswith(b'\xd0\xcf\x11\xe0'):
                return 'hwp'
    except Exception:
        pass

    # Fallback to file extension
    _, ext = os.path.splitext(input_path.lower())
    if ext == '.hwpx':
        return 'hwpx'
    return 'hwp'

def convert_document(input_path, output_pdf_path):
    """
    Converts .hwp or .hwpx file to PDF with automatic format detection and fallback.
    Returns (success: bool, elapsed_seconds: float, error_message: str or None)
    """
    if not os.path.exists(input_path):
        return False, 0, f"입력 파일을 찾을 수 없습니다: {input_path}"

    start_time = time.time()
    doc_type = detect_document_type(input_path)

    primary_converter = convert_hwpx_to_pdf if doc_type == 'hwpx' else convert_hwp_to_pdf
    secondary_converter = convert_hwp_to_pdf if doc_type == 'hwpx' else convert_hwpx_to_pdf

    primary_error = None
    # 1. Try primary detected converter
    try:
        success = primary_converter(input_path, output_pdf_path)
        if success and os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
            elapsed = round(time.time() - start_time, 2)
            return True, elapsed, None
    except Exception as e:
        primary_error = str(e)

    # 2. If primary converter fails, automatically try the secondary converter as fallback
    try:
        success = secondary_converter(input_path, output_pdf_path)
        if success and os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
            elapsed = round(time.time() - start_time, 2)
            return True, elapsed, None
    except Exception as e:
        secondary_error = str(e)
        elapsed = round(time.time() - start_time, 2)
        return False, elapsed, f"변환 실패 (시도 1: {primary_error}, 시도 2: {secondary_error})"

    elapsed = round(time.time() - start_time, 2)
    return False, elapsed, f"변환 결과 파일이 비어있습니다. ({primary_error or '오류'})"
