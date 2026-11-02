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
    if uri.startswith('file://'):
        path = uri[7:]
    else:
        path = uri

    if os.path.exists(path):
        return path
    
    # Check fonts directory
    test_font = os.path.join(FONT_DIR, os.path.basename(path))
    if os.path.exists(test_font):
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
        self.char_styles = {} # id -> style dict
        self.para_styles = {} # id -> style dict
        self.images = {}      # binaryId -> base64 data uri
        self.font_faces = {}  # id -> font family name

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

    def _parse_run(self, run_elem, char_pr_id):
        style_dict = self.char_styles.get(char_pr_id, {})
        style_str = "; ".join([f"{k}: {v}" for k, v in style_dict.items()])
        
        run_parts = []
        for child in run_elem:
            tag = clean_tag(child.tag)
            if tag == 't':
                text = child.text or ""
                # Escape html
                text = (text.replace('&', '&amp;')
                            .replace('<', '&lt;')
                            .replace('>', '&gt;')
                            .replace('"', '&quot;')
                            .replace('\n', '<br/>'))
                run_parts.append(text)
            elif tag == 'tbl':
                run_parts.append(self._parse_table(child))
            elif tag == 'pic':
                pic_html = self._parse_picture(child)
                if pic_html:
                    run_parts.append(pic_html)
            elif tag == 'lineBreak':
                run_parts.append('<br/>')

        inner = "".join(run_parts)
        if not inner:
            return ""
        if style_str:
            return f'<span style="{style_str}">{inner}</span>'
        return inner

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

        if bin_id and bin_id in self.images:
            data_uri = self.images[bin_id]
            return f'<img src="{data_uri}" style="max-width: 95%; height: auto; display: block; margin: 8px auto;" />'
        return ""

    def _parse_table(self, tbl_elem):
        # Parse table structure
        rows_html = []
        for child in tbl_elem:
            tag = clean_tag(child.tag)
            if tag == 'tr':
                cells_html = []
                for tc in child:
                    tc_tag = clean_tag(tc.tag)
                    if tc_tag == 'tc':
                        col_span = tc.attrib.get('colSpan', '1')
                        row_span = tc.attrib.get('rowSpan', '1')
                        
                        cell_content = []
                        # tc can contain subList -> p
                        for sub in tc.iter():
                            if clean_tag(sub.tag) == 'p':
                                cell_content.append(self._parse_paragraph(sub))

                        cell_inner = "".join(cell_content) or "&nbsp;"
                        span_attrs = ""
                        if col_span and col_span != '1':
                            span_attrs += f' colspan="{col_span}"'
                        if row_span and row_span != '1':
                            span_attrs += f' rowspan="{row_span}"'
                            
                        cells_html.append(f'<td{span_attrs} class="tbl-cell">{cell_inner}</td>')
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
    margin: 20mm 15mm 20mm 15mm;
}}
@font-face {{
    font-family: 'AppleGothic';
    src: url('{FINAL_FONT_PATH}');
}}
body {{
    font-family: 'AppleGothic', sans-serif;
    color: #111827;
    font-size: 10.5pt;
    line-height: 1.6;
    margin: 0;
    padding: 0;
}}
.paragraph {{
    min-height: 1.2em;
    word-break: break-all;
}}
.doc-table {{
    width: 100%;
    margin: 10px 0;
    page-break-inside: avoid;
}}
.tbl-cell {{
    border: 0.8pt solid #64748b;
    padding: 6px 8px;
    vertical-align: middle;
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

