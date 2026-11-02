import os
import time
from hwpx_converter import convert_hwpx_to_pdf
from hwp_converter import convert_hwp_to_pdf

ALLOWED_EXTENSIONS = {'.hwp', '.hwpx'}

def is_allowed_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS

def convert_document(input_path, output_pdf_path):
    """
    Converts .hwp or .hwpx file to PDF.
    Returns (success: bool, elapsed_seconds: float, error_message: str or None)
    """
    if not os.path.exists(input_path):
        return False, 0, f"입력 파일을 찾을 수 없습니다: {input_path}"

    _, ext = os.path.splitext(input_path.lower())
    if ext not in ALLOWED_EXTENSIONS:
        return False, 0, f"지원하지 않는 파일 형식입니다 ({ext}). .hwp 또는 .hwpx 파일만 가능합니다."

    start_time = time.time()
    try:
        if ext == '.hwpx':
            success = convert_hwpx_to_pdf(input_path, output_pdf_path)
            if not success or not os.path.exists(output_pdf_path) or os.path.getsize(output_pdf_path) == 0:
                return False, round(time.time() - start_time, 2), "HWPX 변환 중 오류가 발생했습니다."
        elif ext == '.hwp':
            success = convert_hwp_to_pdf(input_path, output_pdf_path)
            if not success or not os.path.exists(output_pdf_path) or os.path.getsize(output_pdf_path) == 0:
                return False, round(time.time() - start_time, 2), "HWP 변환 중 오류가 발생했습니다."
        else:
            return False, 0, "알 수 없는 형식"

        elapsed = round(time.time() - start_time, 2)
        return True, elapsed, None
    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return False, elapsed, str(e)
