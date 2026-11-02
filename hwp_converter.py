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
    if 'AppleGothic' in uri or uri.endswith('.ttf'):
        return FONT_PATH

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
    Convert .hwp to PDF using pure python pyhwp API + xhtml2pdf with layout sanitization
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

        # Read external styles.css if present and inline it
        css_file = os.path.join(dest_dir, "styles.css")
        external_css = ""
        if os.path.exists(css_file):
            with open(css_file, 'r', encoding='utf-8', errors='replace') as f:
                external_css = f.read()
            # Remove font-family overrides from styles.css so our Korean font applies cleanly
            external_css = re.sub(r'font-family:[^;]+;', '', external_css)

        # Remove external stylesheet link tag to avoid pisa warning
        html = re.sub(r'<link[^>]*href=[\"\']styles\.css[\"\'][^>]*/?>', '', html)

        # 1. Sanitize outer wrapper divs that break ReportLab flowable page-splitting:
        # e.g., <div class="Section Section-0 Paper">, <div class="HeaderPageFooter">, <div class="Page">
        cleaned = re.sub(r'<div\s+class=[\"\'](Section[^\"\']*|HeaderPageFooter|Page)[\"\']>', '', html)
        # Strip TableControl span wrappers that force indivisible BlockBox
        cleaned = re.sub(r'</?span[^>]*TableControl[^>]*>', '', cleaned)

        # 2. Neutralize fixed width/positions that cause LayoutError
        cleaned = re.sub(r'width:\s*210mm;?', 'width: 100%;', cleaned)
        cleaned = re.sub(r'position:\s*relative;?', '', cleaned)
        cleaned = re.sub(r'display:\s*inline-block;?', 'display: block;', cleaned)

        # Convert table and td fixed mm widths into proportional percentage widths
        # and strip fixed height on td (which in xhtml2pdf sets FONTSIZE/LEADING to 1.0!)
        def _normalize_table(match):
            tbl_html = match.group(0)
            m_tw = re.search(r'<table[^>]*style="[^"]*width:\s*([\d.]+)mm', tbl_html)
            tbl_w = float(m_tw.group(1)) if m_tw else 180.0
            if tbl_w <= 0:
                tbl_w = 180.0

            def _fix_td(td_match):
                td_tag = td_match.group(0)
                # Remove fixed height on td so xhtml2pdf does not force FONTSIZE=1.0
                td_tag = re.sub(r'height:\s*[\d.]+mm;?\s*', '', td_tag)
                # Convert width: XXmm to proportional percentage
                m_cw = re.search(r'width:\s*([\d.]+)mm;?', td_tag)
                if m_cw:
                    cw = float(m_cw.group(1))
                    pct = max(2.5, min(100.0, (cw / tbl_w) * 100.0))
                    td_tag = re.sub(r'width:\s*[\d.]+mm;?', f'width: {pct:.1f}%;', td_tag)
                return td_tag

            tbl_html = re.sub(r'<td[^>]*>', _fix_td, tbl_html)
            tbl_html = re.sub(r'(<table[^>]*style="[^"]*)width:\s*[\d.]+mm;?', r'\1width: 100%;', tbl_html)
            return tbl_html

        cleaned = re.sub(r'<table[^>]*>.*?</table>', _normalize_table, cleaned, flags=re.S)

        # 3. Add clean page styles with proper Korean font support
        korean_style = f"""
        <style>
        @page {{
            size: A4 portrait;
            margin: 12mm 12mm 12mm 12mm;
        }}
        @font-face {{
            font-family: '{FONT_NAME}';
            src: url('{FONT_PATH}');
        }}
        {external_css}
        body, p, div, span, table, td, th {{
            font-family: '{FONT_NAME}', sans-serif !important;
            -pdf-word-wrap: CJK;
        }}
        body {{
            background-color: #ffffff !important;
            margin: 0 !important;
            padding: 0 !important;
        }}
        .Paper {{
            border: none !important;
            margin: 0 !important;
            padding: 0 !important;
        }}
        table {{
            width: 100% !important;
            max-width: 100% !important;
            border-collapse: collapse;
            -pdf-word-wrap: CJK;
        }}
        td, th {{
            border: 0.5pt solid #475569;
            padding: 2px 2px;
            vertical-align: middle;
            word-break: break-all;
            -pdf-word-wrap: CJK;
        }}
        td p, th p {{
            margin: 0 !important;
            padding: 0 !important;
            line-height: 1.25 !important;
            -pdf-word-wrap: CJK;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        .HeaderPageFooter, .Page, .Section {{
            width: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }}
        </style>
        """
        
        if '<head>' in cleaned:
            cleaned = cleaned.replace('<head>', f'<head>{korean_style}')
        else:
            cleaned = f"{korean_style}\n{cleaned}"

        with open(output_pdf_file, 'wb') as f:
            pisa_status = pisa.CreatePDF(cleaned, dest=f, link_callback=link_callback)

        if pisa_status.err:
            raise RuntimeError("xhtml2pdf conversion had errors")

        return True


def convert_hwp_via_text_fallback(hwp_file, output_pdf_file):
    """
    Fallback converter: extract text and generate clean PDF using ReportLab
    """
    ensure_font_registered()
    from hwp5.xmlmodel import Hwp5File
    from hwp5.hwp5txt import TextTransform

    text = ""
    try:
        # In Python 3, lxml writes bytes, so BytesIO is required
        text_buf = io.BytesIO()
        text_transform = TextTransform()
        with closing(Hwp5File(hwp_file)) as hwp5file:
            text_transform.transform_hwp5_to_text(hwp5file, text_buf)
        text = text_buf.getvalue().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"TextTransform error: {e}")

    if not text.strip():
        # Fallback to olefile extraction if TextTransform is empty or failed
        import olefile
        import zlib
        try:
            if olefile.isOleFile(hwp_file):
                with olefile.OleFileIO(hwp_file) as ole:
                    for entry in ole.listdir():
                        if entry and entry[0] == 'BodyText':
                            stream = ole.openstream(entry)
                            data = stream.read()
                            try:
                                data = zlib.decompress(data, -15)
                            except Exception:
                                pass
                            text += data.decode('utf-16le', errors='ignore') + "\n"
        except Exception as ole_err:
            print(f"olefile BodyText fallback error: {ole_err}")

    if not text.strip():
        raise RuntimeError("문서에서 텍스트 내용을 추출할 수 없습니다.")

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
