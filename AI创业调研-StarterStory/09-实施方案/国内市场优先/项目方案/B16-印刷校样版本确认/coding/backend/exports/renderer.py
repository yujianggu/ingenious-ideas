"""No-network OS sandbox plus an allowlist URL fetcher and resource budgets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from django.conf import settings
from .service import ExportFailure


def sandbox_command(work, command):
    work = Path(work).resolve()
    runtime = {str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}
    if sys.platform == 'darwin':
        executable = shutil.which('sandbox-exec')
        if not executable:
            raise ExportFailure('render_sandbox_unavailable')
        reads = runtime | {'/System', '/usr/lib', '/usr/share', '/Library/Fonts', '/dev/null', '/dev/urandom', '/usr/bin/env', str(work)}
        # The operator's fontconfig and shared-library paths are trusted runtime
        # configuration; no document-controlled filesystem path is accepted.
        for key in ['DYLD_FALLBACK_LIBRARY_PATH']:
            reads.update(str(Path(p).resolve()) for p in os.environ.get(key, '').split(':') if p)
        fontconf = os.environ.get('FONTCONFIG_FILE')
        if fontconf:
            reads.add(str(Path(fontconf).resolve()))
        profile = '(version 1)(allow default)(deny network*)(deny file-read*)(allow file-read-metadata)(allow file-read-data (literal "/"))(deny file-write*)'
        profile += '(allow file-read* ' + ' '.join('(subpath ' + json.dumps(p) + ')' for p in sorted(reads)) + ')'
        profile += '(allow file-write* (subpath ' + json.dumps(str(work)) + ') (literal "/dev/null"))'
        # macOS clears DYLD variables at the protected sandbox-exec boundary.
        # Reapply this trusted runtime setting after entering the sandbox.
        dyld = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH')
        if dyld:
            command = ['/usr/bin/env', 'DYLD_FALLBACK_LIBRARY_PATH=' + dyld, *command]
        return [executable, '-p', profile, *command]
    if sys.platform.startswith('linux'):
        executable = shutil.which('bwrap')
        if not executable:
            raise ExportFailure('render_sandbox_unavailable')
        result = [executable, '--die-with-parent', '--unshare-all', '--new-session', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        for path in sorted(runtime | {'/usr', '/lib', '/lib64', '/etc/fonts', '/etc/ld.so.cache'}):
            if Path(path).exists():
                result += ['--ro-bind', path, path]
        result += ['--bind', str(work), str(work), '--chdir', str(work), *command]
        return result
    raise ExportFailure('render_sandbox_unavailable')


def render_pdf(html):
    encoded = html.encode('utf-8')
    if len(encoded) > settings.EXPORT_MAX_BYTES * 2:
        raise ExportFailure('export_size_limit')
    with tempfile.TemporaryDirectory(prefix='b16-render-') as folder:
        work = Path(folder)
        (work / 'input.html').write_bytes(encoded)
        shutil.copyfile(Path(__file__).with_name('render_child.py'), work / 'render_child.py')
        command = [sys.executable, '-I', str(work / 'render_child.py'), str(work),
                   str(settings.EXPORT_MAX_PAGES), str(settings.EXPORT_MAX_BYTES), str(settings.EXPORT_RENDER_MEMORY_MB)]
        env = {key: value for key, value in os.environ.items() if key in ['PATH', 'DYLD_FALLBACK_LIBRARY_PATH', 'FONTCONFIG_FILE', 'LANG', 'LC_ALL']}
        # A task-local font cache avoids writes outside the sandbox.
        font_dirs = ['/System/Library/Fonts', '/Library/Fonts'] if sys.platform == 'darwin' else ['/usr/share/fonts', '/usr/local/share/fonts']
        fonts = '<fontconfig>' + ''.join('<dir>' + p + '</dir>' for p in font_dirs) + '<cachedir>' + str(work / 'font-cache') + '</cachedir><alias><family>sans-serif</family><prefer><family>Arial Unicode MS</family><family>Noto Sans CJK SC</family></prefer></alias></fontconfig>'
        (work / 'fonts.conf').write_text(fonts)
        env.update(HOME=str(work), TMPDIR=str(work), XDG_CACHE_HOME=str(work), FONTCONFIG_FILE=str(work / 'fonts.conf'))
        with subprocess.Popen(sandbox_command(work, command), cwd=work, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
            deadline = time.monotonic() + settings.EXPORT_RENDER_TIMEOUT
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    child.kill(); child.wait()
                    raise ExportFailure('render_timeout')
                # macOS does not enforce RLIMIT_AS reliably. Bound RSS from the
                # trusted parent as well; Linux additionally uses RLIMIT_AS.
                rss = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(child.pid)], capture_output=True, timeout=2)
                value = rss.stdout.strip()
                if value and int(value) > settings.EXPORT_RENDER_MEMORY_MB * 1024:
                    child.kill(); child.wait()
                    raise ExportFailure('render_memory_limit')
                time.sleep(0.05)
            if child.returncode != 0:
                raise ExportFailure('render_failed')
        target = work / 'output.pdf'
        if not target.exists() or target.stat().st_size > settings.EXPORT_MAX_BYTES:
            raise ExportFailure('render_failed')
        return target.read_bytes()
