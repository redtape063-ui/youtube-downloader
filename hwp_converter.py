import os
import io
import tempfile
import re
from contextlib import closing
import patch_xhtml2pdf  # Fix xhtml2pdf splitLongWords bytes join bug
from xhtml2pdf import pisa

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register Korean font for ReportLab
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(CURRENT_DIR, 'fonts')
LOCAL_FONT_PATH = os.path.join(FONT_DIR, 'AppleGothic.ttf')
SYSTEM_FONT_PATH = '/System/Library/Fonts/Supplemental/AppleGothic.ttf'
FINAL_FONT_PATH = LOCAL_FONT_PATH if os.path.exists(LOCAL_FONT_PATH) else SYSTEM_FONT_PATH

FONT_REGISTERED = False
FONT_NAME = 'AppleGothic'
FONT_PATH = FINAL_FONT_PATH

def link_callback(uri, rel):
    """Resolve font and image resources safely for xhtml2pdf"""
    if uri.startswith('file://'):
        path = uri[7:]
    else:
        path = uri

    if os.path.exists(path):
        return path
    
    test_font = os.path.join(FONT_DIR, os.path.basename(path))
    if os.path.exists(test_font):
        return test_font

    return uri


def ensure_font_registered():
    global FONT_REGISTERED
    if not FONT_REGISTERED:
        if os.path.exists(FONT_PATH):
            pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
            FONT_REGISTERED = True

def convert_hwp_via_html(hwp_file, output_pdf_file):
    """
    Convert .hwp to PDF using pure python pyhwp API + xhtml2pdf
    """
    from hwp5.xmlmodel import Hwp5File
    from hwp5.hwp5html import HTMLTransform

    with tempfile.TemporaryDirectory() as tmpdir:
        dest_dir = os.path.join(tmpdir, "html_out")
        os.makedirs(dest_dir, exist_ok=True)
        
        html_transform = HTMLTransform()
        with closing(Hwp5File(hwp_file)) as hwp5file:
            html_transform.transform_hwp5_to_dir(hwp5file, dest_dir)

        index_html = os.path.join(dest_dir, "index.xhtml")
        if not os.path.exists(index_html):
            index_html = os.path.join(dest_dir, "index.html")

        if not os.path.exists(index_html):
            raise RuntimeError("hwp html output not generated")

        with open(index_html, 'r', encoding='utf-8', errors='replace') as f:
            html = f.read()

        korean_style = f"""
        <style>
        @font-face {{
            font-family: '{FONT_NAME}';
            src: url('{FONT_PATH}');
        }}
        body, p, div, span, table, td, th {{
            font-family: '{FONT_NAME}', sans-serif !important;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
        }}
        td, th {{
            border: 0.5pt solid #718096;
            padding: 4px;
        }}
        img {{
            max-width: 100%;
        }}
        </style>
        """
        
        if '<head>' in html:
            html = html.replace('<head>', f'<head>{korean_style}')
        else:
            html = f"{korean_style}\n{html}"

        with open(output_pdf_file, 'wb') as f:
            pisa_status = pisa.CreatePDF(html, dest=f, link_callback=link_callback)

        if pisa_status.err:
            raise RuntimeError("xhtml2pdf conversion had errors")

        return True

def convert_hwp_via_text_fallback(hwp_file, output_pdf_file):
    """
    Fallback converter: extract text via pure python pyhwp TextTransform and generate clean PDF using ReportLab
    """
    ensure_font_registered()
    from hwp5.xmlmodel import Hwp5File
    from hwp5.hwp5txt import TextTransform

    text_buf = io.StringIO()
    text_transform = TextTransform()
    with closing(Hwp5File(hwp_file)) as hwp5file:
        text_transform.transform_hwp5_to_text(hwp5file, text_buf)

    text = text_buf.getvalue()
    if not text.strip():
        # Fallback to olefile extraction if TextTransform is empty
        import olefile
        import zlib
        if olefile.isOleFile(hwp_file):
            with olefile.OleFileIO(hwp_file) as ole:
                for entry in ole.listdir():
                    if entry[0] == 'BodyText':
                        stream = ole.openstream(entry)
                        data = stream.read()
                        try:
                            data = zlib.decompress(data, -15)
                        except:
                            pass
                        text += data.decode('utf-16le', errors='ignore') + "\n"

    doc = SimpleDocTemplate(
        output_pdf_file,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        'KoreanNormal',
        parent=styles['Normal'],
        fontName=FONT_NAME if FONT_REGISTERED else 'Helvetica',
        fontSize=10.5,
        leading=16,
        spaceAfter=4,
        wordWrap='CJK'
    )

    story = []
    lines = text.split('\n')
    for line in lines:
        cleaned = (line.replace('&', '&amp;')
                       .replace('<', '&lt;')
                       .replace('>', '&gt;')
                       .strip())
        if not cleaned:
            story.append(Spacer(1, 8))
        else:
            story.append(Paragraph(cleaned, normal_style))

    doc.build(story)
    return True


def convert_hwp_to_pdf(hwp_file, output_pdf_file):
    """
    Main HWP to PDF conversion entrypoint.
    Tries HTML-based rich rendering first, falls back to text-based if needed.
    """
    try:
        convert_hwp_via_html(hwp_file, output_pdf_file)
        return True
    except Exception as e:
        print(f"Rich HWP conversion failed ({e}), attempting fallback...")
        convert_hwp_via_text_fallback(hwp_file, output_pdf_file)
        return True
