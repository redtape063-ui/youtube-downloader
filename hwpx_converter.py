import os
import zipfile
import base64
import re
from xml.etree import ElementTree as ET
import patch_xhtml2pdf  # Fix xhtml2pdf splitLongWords bytes join bug


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(CURRENT_DIR, 'fonts')
LOCAL_FONT_PATH = os.path.join(FONT_DIR, 'AppleGothic.ttf')

# Fallback fonts
SYSTEM_FONT_PATH = '/System/Library/Fonts/Supplemental/AppleGothic.ttf'
FINAL_FONT_PATH = LOCAL_FONT_PATH if os.path.exists(LOCAL_FONT_PATH) else SYSTEM_FONT_PATH

def link_callback(uri, rel):
    """Resolve font and image resources safely for xhtml2pdf"""
    if uri.startswith('data:'):
        return uri

    if uri.startswith('file://'):
        path = uri[7:]
    else:
        path = uri

    if os.path.exists(path) and os.path.isfile(path):
        return path
    
    # Check fonts directory
    basename = os.path.basename(path)
    if basename:
        test_font = os.path.join(FONT_DIR, basename)
        if os.path.exists(test_font) and os.path.isfile(test_font):
            return test_font

    return uri

NAMESPACES = {
    'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph',
    'hs': 'http://www.hancom.co.kr/hwpml/2011/section',
    'hc': 'http://www.hancom.co.kr/hwpml/2011/core',
    'hh': 'http://www.hancom.co.kr/hwpml/2011/head',
    'opf': 'http://www.idpf.org/2007/opf'
}

def clean_tag(tag):
    """Remove namespace URI from tag name"""
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag

class HwpxParser:
    def __init__(self, hwpx_path):
        self.hwpx_path = hwpx_path
        self.char_styles = {}   # id -> style dict
        self.para_styles = {}   # id -> style dict
        self.border_fills = {}  # id -> border/bg css dict
        self.images = {}        # binaryId -> base64 data uri
        self.font_faces = {}    # id -> font family name

    def parse(self):
        with zipfile.ZipFile(self.hwpx_path, 'r') as z:
            namelist = z.namelist()
            
            # 1. Parse Images in BinData/
            for name in namelist:
                if name.startswith('BinData/'):
                    ext = os.path.splitext(name)[1].lower()
                    mime_types = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif',
                        '.bmp': 'image/bmp'
                    }
                    mime = mime_types.get(ext, 'image/png')
                    bdata = z.read(name)
                    b64 = base64.b64encode(bdata).decode('ascii')
                    bin_id = os.path.basename(name)
                    self.images[bin_id] = f"data:{mime};base64,{b64}"
                    # also map by base name without ext if needed
                    base_no_ext = os.path.splitext(bin_id)[0]
                    self.images[base_no_ext] = f"data:{mime};base64,{b64}"

            # 2. Parse Header (styles)
            header_name = next((n for n in namelist if 'header.xml' in n.lower()), None)
            if header_name:
                self._parse_header(z.read(header_name))

            # 3. Parse Sections (content)
            section_names = sorted([n for n in namelist if re.search(r'section\d+\.xml', n, re.I)])
            if not section_names:
                section_names = sorted([n for n in namelist if 'section' in n.lower() and n.endswith('.xml')])

            body_html_parts = []
            for sec_name in section_names:
                sec_xml = z.read(sec_name)
                body_html_parts.append(self._parse_section(sec_xml))

            full_body = "\n".join(body_html_parts)
            return self._wrap_html(full_body)

    def _parse_header(self, xml_bytes):
        try:
            root = ET.fromstring(xml_bytes)
            # Find charProperties
            for char_pr in root.iter():
                tag = clean_tag(char_pr.tag)
                if tag == 'charPr':
                    c_id = char_pr.attrib.get('id')
                    height = char_pr.attrib.get('height') # in 1/100 pt
                    color = char_pr.attrib.get('textColor') # #RRGGBB or #AARRGGBB
                    bold = char_pr.attrib.get('bold') == '1' or char_pr.attrib.get('bold') == 'true'
                    italic = char_pr.attrib.get('italic') == '1' or char_pr.attrib.get('italic') == 'true'
                    underline = char_pr.attrib.get('underline') not in (None, 'NONE', 'none', '0')
                    
                    font_size = "10.5pt"
                    if height:
                        try:
                            font_size = f"{float(height)/100:.1f}pt"
                        except:
                            pass

                    css_color = None
                    if color:
                        if color.startswith('#'):
                            if len(color) == 9: # #AARRGGBB
                                css_color = '#' + color[3:]
                            else:
                                css_color = color
                        else:
                            try:
                                # integer representation
                                c_int = int(color)
                                css_color = f"#{c_int:06x}"
                            except:
                                pass

                    self.char_styles[c_id] = {
                        'font-size': font_size,
                        'font-weight': 'bold' if bold else 'normal',
                        'font-style': 'italic' if italic else 'normal',
                        'text-decoration': 'underline' if underline else 'none',
                        'color': css_color or '#000000'
                    }

            # Find paraProperties
            for para_pr in root.iter():
                tag = clean_tag(para_pr.tag)
                if tag == 'paraPr':
                    p_id = para_pr.attrib.get('id')
                    align_val = para_pr.attrib.get('align', 'JUSTIFY')
                    
                    # Alignment mapping
                    css_align = 'left'
                    if 'CENTER' in align_val.upper():
                        css_align = 'center'
                    elif 'RIGHT' in align_val.upper():
                        css_align = 'right'
                    elif 'JUSTIFY' in align_val.upper():
                        css_align = 'justify'

                    # Margin / Line spacing
                    line_spacing = "160%"
                    margin_top = "0pt"
                    margin_bottom = "3pt"
                    
                    for child in para_pr:
                        c_tag = clean_tag(child.tag)
                        if c_tag == 'lineSpacing':
                            val = child.attrib.get('value')
                            if val:
                                try:
                                    line_spacing = f"{float(val)}%"
                                except:
                                    pass
                        elif c_tag == 'margin':
                            pass

                    self.para_styles[p_id] = {
                        'text-align': css_align,
                        'line-height': line_spacing,
                        'margin-top': margin_top,
                        'margin-bottom': margin_bottom
                    }

            # Find borderFills
            for bf in root.iter():
                tag = clean_tag(bf.tag)
                if tag == 'borderFill':
                    bf_id = bf.attrib.get('id')
                    bf_css = {}
                    side_map = {
                        'leftBorder': 'border-left',
                        'rightBorder': 'border-right',
                        'topBorder': 'border-top',
                        'bottomBorder': 'border-bottom'
                    }
                    for child in bf:
                        c_tag = clean_tag(child.tag)
                        if c_tag in side_map:
                            b_type = child.attrib.get('type', 'NONE').upper()
                            b_color = child.attrib.get('color', '#000000')
                            if b_color.startswith('#') and len(b_color) == 9:
                                b_color = '#' + b_color[3:]
                            if b_type == 'NONE':
                                bf_css[side_map[c_tag]] = 'none'
                            else:
                                bf_css[side_map[c_tag]] = f'0.6pt solid {b_color}'
                        elif c_tag == 'fillBrush':
                            for fc in child:
                                if clean_tag(fc.tag) == 'winBrush':
                                    face_color = fc.attrib.get('faceColor', 'none')
                                    if face_color and face_color.lower() != 'none':
                                        if face_color.startswith('#') and len(face_color) == 9:
                                            face_color = '#' + face_color[3:]
                                        bf_css['background-color'] = face_color
                    self.border_fills[bf_id] = bf_css
        except Exception as e:
            print(f"Error parsing header: {e}")

    def _parse_section(self, xml_bytes):
        try:
            root = ET.fromstring(xml_bytes)
            html_parts = []
            
            # Root is typically <hs:sec>
            for elem in root:
                elem_tag = clean_tag(elem.tag)
                if elem_tag == 'p':
                    p_html = self._parse_paragraph(elem)
                    if p_html:
                        html_parts.append(p_html)
            return "\n".join(html_parts)
        except Exception as e:
            print(f"Error parsing section: {e}")
            return "<p>문서 섹션 파싱 오류</p>"

    def _parse_paragraph(self, p_elem):
        para_pr_id = p_elem.attrib.get('paraPrIDRef', '')
        style_dict = self.para_styles.get(para_pr_id, {
            'text-align': 'left',
            'line-height': '160%',
            'margin-top': '2pt',
            'margin-bottom': '4pt'
        })
        
        style_str = "; ".join([f"{k}: {v}" for k, v in style_dict.items()])
        
        inner_content = []
        for child in p_elem:
            tag = clean_tag(child.tag)
            if tag == 'run':
                char_pr_id = child.attrib.get('charPrIDRef', '')
                run_html = self._parse_run(child, char_pr_id)
                inner_content.append(run_html)
            elif tag == 'linesegarray':
                continue
            elif tag == 'tbl':
                inner_content.append(self._parse_table(child))

        content_str = "".join(inner_content)
        if not content_str.strip():
            content_str = "&nbsp;"
        return f'<div class="paragraph" style="{style_str}">{content_str}</div>'

    def _escape_html_text(self, text):
        if not text:
            return ""
        text = (text.replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;')
                    .replace('"', '&quot;')
                    .replace('\n', '<br/>'))
        # Preserve multiple consecutive spaces
        text = re.sub(r'  +', lambda m: '&nbsp;' * len(m.group(0)), text)
        return text

    def _extract_t_content(self, t_elem):
        parts = []
        if t_elem.text:
            parts.append(self._escape_html_text(t_elem.text))
        for child in t_elem:
            c_tag = clean_tag(child.tag)
            if c_tag == 'tab':
                parts.append('&nbsp;&nbsp;&nbsp;&nbsp;')
            elif c_tag == 'fwSpace':
                parts.append('&nbsp;&nbsp;')
            elif c_tag == 'lineBreak':
                parts.append('<br/>')
            elif c_tag == 'nbSpace':
                parts.append('&nbsp;')
            if child.text:
                parts.append(self._escape_html_text(child.text))
            if child.tail:
                parts.append(self._escape_html_text(child.tail))
        return "".join(parts)

    def _parse_run(self, run_elem, char_pr_id):
        style_dict = self.char_styles.get(char_pr_id, {})
        style_str = "; ".join([f"{k}: {v}" for k, v in style_dict.items()])
        
        run_parts = []
        for child in run_elem:
            tag = clean_tag(child.tag)
            if tag == 't':
                run_parts.append(self._extract_t_content(child))
            elif tag == 'tbl':
                run_parts.append(self._parse_table(child))
            elif tag == 'pic':
                pic_html = self._parse_picture(child)
                if pic_html:
                    run_parts.append(pic_html)
            elif tag == 'rect':
                rect_html = self._parse_rect(child)
                if rect_html:
                    run_parts.append(rect_html)
            elif tag == 'container':
                cont_html = self._parse_container(child)
                if cont_html:
                    run_parts.append(cont_html)
            elif tag == 'lineBreak':
                run_parts.append('<br/>')

        inner = "".join(run_parts)
        if not inner:
            return ""
        if style_str:
            return f'<span style="{style_str}">{inner}</span>'
        return inner

    def _parse_rect(self, rect_elem):
        """Extract text paragraphs inside drawing rectangle / text box (<hp:rect> -> <hp:drawText> -> <hp:subList> -> <hp:p>)"""
        w_val = 20000.0
        for child in rect_elem:
            if clean_tag(child.tag) in ('curSz', 'sz'):
                try:
                    w_val = float(child.attrib.get('width', '20000'))
                    break
                except Exception:
                    pass

        # Small inline box (e.g. [ ㉠ ]) vs full-width question prompt box
        if w_val < 8000:
            inline_texts = []
            for t_el in rect_elem.iter():
                if clean_tag(t_el.tag) == 't':
                    txt = self._extract_t_content(t_el).strip()
                    if txt:
                        inline_texts.append(txt)
            if inline_texts:
                return f'<b>[{"".join(inline_texts)}]</b> '
            return ""

        paras = []
        for child in rect_elem:
            if clean_tag(child.tag) == 'drawText':
                for sub in child:
                    if clean_tag(sub.tag) == 'subList':
                        for p in sub:
                            if clean_tag(p.tag) == 'p':
                                paras.append(self._parse_paragraph(p))
        if not paras:
            return ""
        inner = "".join(paras)
        return f'<div class="rect-box">{inner}</div>'

    def _parse_container(self, cont_elem):
        """Extract pictures inside grouped container (<hp:container>)"""
        parts = []
        for child in cont_elem:
            c_tag = clean_tag(child.tag)
            if c_tag == 'pic':
                pic_html = self._parse_picture(child)
                if pic_html:
                    parts.append(pic_html)
            elif c_tag == 'container':
                sub_html = self._parse_container(child)
                if sub_html:
                    parts.append(sub_html)
        return "".join(parts)

    def _parse_picture(self, pic_elem):
        # Find img element or binaryItemIDRef
        bin_id = None
        for child in pic_elem.iter():
            tag = clean_tag(child.tag)
            if tag == 'img':
                bin_id = child.attrib.get('binaryItemIDRef')
                break
        
        if not bin_id:
            bin_id = pic_elem.attrib.get('binaryItemIDRef')

        # Check size in HWPUNIT (7200 HWPUNIT = 1 inch = 25.4mm)
        width_style = "max-width: 90%; height: auto;"
        for child in pic_elem:
            if clean_tag(child.tag) in ('curSz', 'sz'):
                w_val = child.attrib.get('width')
                if w_val:
                    try:
                        w_mm = float(w_val) / 7200.0 * 25.4
                        if 5.0 <= w_mm <= 175.0:
                            width_style = f"width: {w_mm:.1f}mm; max-width: 95%; height: auto;"
                            break
                    except Exception:
                        pass

        if bin_id and bin_id in self.images:
            data_uri = self.images[bin_id]
            return f'<img src="{data_uri}" style="{width_style} display: block; margin: 4px auto;" />'
        return ""

    def _parse_table(self, tbl_elem):
        # First pass: collect rows and compute total table width in HWPUNIT
        raw_rows = []
        max_row_width = 0.0

        for child in tbl_elem:
            if clean_tag(child.tag) == 'tr':
                row_cells = []
                row_width = 0.0
                for tc in child:
                    if clean_tag(tc.tag) == 'tc':
                        col_span = tc.attrib.get('colSpan', '1')
                        row_span = tc.attrib.get('rowSpan', '1')
                        bf_id = tc.attrib.get('borderFillIDRef', '')
                        cell_w = 0.0
                        cell_paragraphs = []

                        for sub in tc:
                            st = clean_tag(sub.tag)
                            if st == 'cellSpan':
                                col_span = sub.attrib.get('colSpan', col_span)
                                row_span = sub.attrib.get('rowSpan', row_span)
                            elif st == 'cellSz':
                                try:
                                    cell_w = float(sub.attrib.get('width', '0'))
                                except Exception:
                                    cell_w = 0.0
                            elif st == 'subList':
                                for p in sub:
                                    if clean_tag(p.tag) == 'p':
                                        cell_paragraphs.append(self._parse_paragraph(p))

                        row_width += cell_w
                        row_cells.append({
                            'colSpan': col_span,
                            'rowSpan': row_span,
                            'bf_id': bf_id,
                            'width': cell_w,
                            'paragraphs': cell_paragraphs
                        })
                if row_width > max_row_width:
                    max_row_width = row_width
                raw_rows.append(row_cells)

        # Second pass: build HTML with proportional widths, colSpan/rowSpan, and border styles
        rows_html = []
        for row_cells in raw_rows:
            cells_html = []
            for cell in row_cells:
                col_span = cell['colSpan']
                row_span = cell['rowSpan']
                bf_id = cell['bf_id']
                cell_w = cell['width']
                cell_inner = "".join(cell['paragraphs']) or "&nbsp;"

                span_attrs = ""
                if col_span and col_span != '1':
                    span_attrs += f' colspan="{col_span}"'
                if row_span and row_span != '1':
                    span_attrs += f' rowspan="{row_span}"'

                cell_styles = []
                if max_row_width > 0 and cell_w > 0:
                    pct = (cell_w / max_row_width) * 100.0
                    cell_styles.append(f'width: {pct:.1f}%')
                    if pct < 4.5:
                        cell_styles.append('padding: 1px 0px')

                bf_css = self.border_fills.get(bf_id)
                if bf_css:
                    for k, v in bf_css.items():
                        cell_styles.append(f'{k}: {v}')

                style_attr = f' style="{"; ".join(cell_styles)}"' if cell_styles else ''
                cells_html.append(f'<td{span_attrs} class="tbl-cell"{style_attr}>{cell_inner}</td>')
            rows_html.append(f'<tr>{"".join(cells_html)}</tr>')

        return f'''
        <table class="doc-table">
            <tbody>
                {"".join(rows_html)}
            </tbody>
        </table>
        '''

    def _wrap_html(self, body_content):
        return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{
    size: A4 portrait;
    margin: 15mm 15mm 15mm 15mm;
}}
@font-face {{
    font-family: 'AppleGothic';
    src: url('{FINAL_FONT_PATH}');
}}
body {{
    font-family: 'AppleGothic', sans-serif;
    color: #111827;
    font-size: 10pt;
    line-height: 1.5;
    margin: 0;
    padding: 0;
    -pdf-word-wrap: CJK;
}}
.paragraph {{
    min-height: 1.1em;
    word-break: break-all;
    -pdf-word-wrap: CJK;
}}
.rect-box {{
    border: 0.7pt solid #374151;
    padding: 6px 8px;
    margin: 6px 0;
    background-color: #f9fafb;
    -pdf-word-wrap: CJK;
}}
.doc-table {{
    width: 100%;
    margin: 6px 0;
    border-collapse: collapse;
    -pdf-word-wrap: CJK;
}}
.tbl-cell {{
    border: 0.6pt solid #475569;
    padding: 3px 4px;
    vertical-align: middle;
    -pdf-word-wrap: CJK;
}}
img {{
    max-width: 100%;
}}
</style>
</head>
<body>
{body_content}
</body>
</html>
'''

def convert_hwpx_to_pdf(hwpx_file, output_pdf_file):
    from xhtml2pdf import pisa
    parser = HwpxParser(hwpx_file)
    html_content = parser.parse()
    
    with open(output_pdf_file, 'wb') as f:
        pisa_status = pisa.CreatePDF(html_content, dest=f, link_callback=link_callback)
    
    return not pisa_status.err

