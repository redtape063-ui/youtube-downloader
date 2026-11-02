import os
import zipfile

def create_sample_hwpx(output_path):
    # Minimal valid OWPML hwpx file
    header_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
    <hh:charProperties>
        <hh:charPr id="0" height="1100" textColor="#000000" bold="0" italic="0"/>
        <hh:charPr id="1" height="1800" textColor="#1E40AF" bold="1" italic="0"/>
        <hh:charPr id="2" height="1400" textColor="#047857" bold="1" italic="0"/>
        <hh:charPr id="3" height="1000" textColor="#4B5563" bold="0" italic="1"/>
    </hh:charProperties>
    <hh:paraProperties>
        <hh:paraPr id="0" align="JUSTIFY"/>
        <hh:paraPr id="1" align="CENTER"/>
        <hh:paraPr id="2" align="LEFT"/>
    </hh:paraProperties>
</hh:head>"""

    section0_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
    <hp:p paraPrIDRef="1">
        <hp:run charPrIDRef="1">
            <hp:t>한글 HWPX 문서 변환 테스트</hp:t>
        </hp:run>
    </hp:p>
    <hp:p paraPrIDRef="1">
        <hp:run charPrIDRef="3">
            <hp:t>HWPX to PDF 웹 변환 서비스 샘플 문서입니다.</hp:t>
        </hp:run>
    </hp:p>
    <hp:p paraPrIDRef="0">
        <hp:run charPrIDRef="2">
            <hp:t>1. 변환 개요 및 특징</hp:t>
        </hp:run>
    </hp:p>
    <hp:p paraPrIDRef="0">
        <hp:run charPrIDRef="0">
            <hp:t>본 웹 애플리케이션은 HWPX 및 HWP 문서를 표준 PDF 포맷으로 빠르고 정확하게 변환해 드립니다. 표, 글자 스타일, 색상, 정렬 등 원본 서식을 깔끔하게 반영합니다.</hp:t>
        </hp:run>
    </hp:p>
    <hp:p paraPrIDRef="0">
        <hp:tbl>
            <hp:tr>
                <hp:tc>
                    <hp:p paraPrIDRef="1"><hp:run charPrIDRef="2"><hp:t>항목</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="1"><hp:run charPrIDRef="2"><hp:t>지원 내용</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="1"><hp:run charPrIDRef="2"><hp:t>상태</hp:t></hp:run></hp:p>
                </hp:tc>
            </hp:tr>
            <hp:tr>
                <hp:tc>
                    <hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>HWPX 포맷</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>OWPML 파싱 기반 고품질 PDF 렌더링</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="1"><hp:run charPrIDRef="0"><hp:t>지원 완료</hp:t></hp:run></hp:p>
                </hp:tc>
            </hp:tr>
            <hp:tr>
                <hp:tc>
                    <hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>HWP 포맷</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="0"><hp:run charPrIDRef="0"><hp:t>HWP 5.0 바이너리 파싱 및 폴백 엔진</hp:t></hp:run></hp:p>
                </hp:tc>
                <hp:tc>
                    <hp:p paraPrIDRef="1"><hp:run charPrIDRef="0"><hp:t>지원 완료</hp:t></hp:run></hp:p>
                </hp:tc>
            </hp:tr>
        </hp:tbl>
    </hp:p>
    <hp:p paraPrIDRef="0">
        <hp:run charPrIDRef="0">
            <hp:t>다양한 스타일 및 서식이 잘 보존되어 출력되는지 확인해보세요!</hp:t>
        </hp:run>
    </hp:p>
</hs:sec>"""

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('Contents/header.xml', header_xml)
        z.writestr('Contents/section0.xml', section0_xml)
        z.writestr('mimetype', 'application/hwp+zip')
        z.writestr('META-INF/container.xml', '<?xml version="1.0" encoding="UTF-8"?><container/>')

if __name__ == '__main__':
    create_sample_hwpx('/tmp/sample.hwpx')
    print("Sample HWPX created at /tmp/sample.hwpx")
