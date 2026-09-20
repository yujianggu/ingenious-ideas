import json
import subprocess
import sys
from pathlib import Path
import pytest


def test_renderer_os_sandbox_denies_network_and_arbitrary_file(tmp_path):
    from exports.renderer import sandbox_command
    secret = tmp_path / 'outside.txt'
    secret.write_text('not allowed')
    work = tmp_path / 'work'
    work.mkdir()
    script = work / 'probe.py'
    script.write_text('import socket,sys\nfor action in [lambda:open(sys.argv[1]).read(),lambda:socket.socket().connect(("127.0.0.1",9))]:\n try: action();print("ALLOWED")\n except PermissionError: print("DENIED")\n')
    result = subprocess.run(sandbox_command(work, [sys.executable, str(script), str(secret)]), capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'DENIED\nDENIED\n'


def test_renderer_long_chinese_twenty_images_and_limits(settings, tmp_path):
    import base64
    import io
    from PIL import Image, ImageDraw
    from pypdf import PdfReader
    from exports.renderer import render_pdf
    from exports.service import record_html, ExportFailure
    image = Image.new('RGB', (900, 250), '#e0f2fe')
    ImageDraw.Draw(image).rectangle((35,35,865,215), outline='#0284c7', width=8)
    image_bytes=io.BytesIO();image.save(image_bytes, 'PNG')
    images=[{'title': f'第 {n+1} 张：印刷色样核对', 'uri':'data:image/png;base64,' + base64.b64encode(image_bytes.getvalue()).decode()} for n in range(20)]
    version = {'id':'00000000-0000-4000-8000-000000000001','uploaded_by':1,'published_by':1,'created_at':'2026-09-20T08:00:00+00:00','sequence':1,'display_name':'中文长文校样.pdf', 'sha256':'a'*64,'request':{'recipient':'客户甲','expires_at':'2026-09-21T08:00:00+00:00','events':[{'decision':'returned','created_at':'2026-09-20T08:30:00+00:00','external_session_id':'00000000-0000-4000-8000-000000000002','verification_method':'receiver_code','reason':'中文校样需核对字体、裁切与色彩。'*350}]}}
    manifest = {'label':'草稿／不完整','incomplete':True,'owner_id':'00000000-0000-4000-8000-000000000003','frozen_at':'2026-09-20T09:00:00+00:00','order':{'title':'长文与二十张图像实测','revision':3,'status':'returned','current_version_id':version['id'],'versions':[version]}}
    html = record_html(manifest, images)
    pdf=render_pdf(html)
    reader=PdfReader(io.BytesIO(pdf))
    assert 1 < len(reader.pages) <= 100
    text = "".join(page.extract_text() for page in reader.pages)
    assert all(f"第 {n+1} 张" in text for n in range(20))
    assert '草稿／不完整' in reader.pages[0].extract_text()
    Path('/tmp/b16-task5-long.pdf').write_bytes(pdf)
    settings.EXPORT_MAX_PAGES=1
    with pytest.raises(ExportFailure, match='render_failed'):
        render_pdf(html)


@pytest.mark.parametrize('uri', ['file:///etc/passwd', 'http://127.0.0.1:9/private', 'https://example.com/secret'])
def test_renderer_rejects_non_data_document_resources(uri):
    from exports.renderer import render_pdf
    from exports.service import ExportFailure
    with pytest.raises(ExportFailure, match='render_failed'):
        render_pdf('<img src="'+uri+'">')


@pytest.mark.parametrize('setting,value,error', [('EXPORT_RENDER_TIMEOUT',0.001,'render_timeout'), ('EXPORT_RENDER_MEMORY_MB',1,'render_memory_limit'), ('EXPORT_MAX_BYTES',32,'render_failed')])
def test_renderer_enforces_resource_budgets(settings, setting, value, error):
    from exports.renderer import render_pdf
    from exports.service import ExportFailure
    setattr(settings,setting,value)
    with pytest.raises(ExportFailure,match=error):
        render_pdf('<p>校样</p>')


def test_chinese_footer_uses_document_font():
    from exports.service import record_html
    from exports.renderer import render_pdf
    from pypdf import PdfReader
    import io
    html=record_html({'label':'草稿／不完整','order':{'title':'脚注核对','versions':[]}},[])
    text=PdfReader(io.BytesIO(render_pdf(html))).pages[0].extract_text()
    assert '第 1 页 / 1 页' in text
