"""Bounded subprocess parser. File content never executes or renders."""
import json
import sys
import warnings
from pathlib import Path


def inspect(path, extension, page_limit, pixel_limit):
    if extension == '.pdf':
        from pypdf import PdfReader
        with open(path, 'rb') as stream:
            if not stream.read(8).startswith(b'%PDF-'):
                raise ValueError('Not PDF')
            stream.seek(0)
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted or not 0 < len(reader.pages) <= page_limit:
                raise ValueError('Unsupported PDF')
            for page in reader.pages:
                if page.mediabox.width <= 0 or page.mediabox.height <= 0:
                    raise ValueError('Invalid page')
            return 'application/pdf'
    if extension in {'.png', '.jpg', '.jpeg'}:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = pixel_limit
        expected = 'PNG' if extension == '.png' else 'JPEG'
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path, formats=[expected]) as img:
                img.verify()
            with Image.open(path, formats=[expected]) as img:
                img.load()
            return 'image/png' if expected == 'PNG' else 'image/jpeg'
    raise ValueError('Unsupported file type')


if __name__ == '__main__':
    # A separate process gives parsing a hard deadline; POSIX also caps CPU and memory.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (ValueError, OSError):
        pass  # macOS does not implement RLIMIT_AS; timeout and pixel/page caps remain.
    try:
        print(json.dumps({'content_type': inspect(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))}))
    except Exception:
        sys.exit(1)
