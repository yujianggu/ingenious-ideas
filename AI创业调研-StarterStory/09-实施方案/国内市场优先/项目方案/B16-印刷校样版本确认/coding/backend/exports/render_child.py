"""Executed only in renderer's OS sandbox, without application secrets or DB."""
import base64
import io
import resource
import sys
from pathlib import Path


def data_only(url, *args, **kwargs):
    # Neither remote URLs, file URLs, SVG nor arbitrary data types are fetched.
    for mime in ['image/png', 'image/jpeg']:
        prefix = 'data:' + mime + ';base64,'
        if url.startswith(prefix):
            payload = base64.b64decode(url[len(prefix):], validate=True)
            if len(payload) > 20 * 1024 * 1024:
                raise ValueError('Image too large')
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = 40_000_000
            with Image.open(io.BytesIO(payload)) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError('Image too large')
                image.verify()
            from weasyprint.urls import URLFetcherResponse
            return URLFetcherResponse(url=url, body=payload, headers={'Content-Type': mime})
    raise ValueError('Resource URL forbidden')


data_only._fail_on_errors = True

def main():
    folder, pages, size, memory = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS, (memory * 1024 * 1024, memory * 1024 * 1024))
    import mimetypes
    mimetypes.knownfiles = []
    mimetypes.init(files=[])
    from weasyprint import HTML
    document = HTML(string=(folder / 'input.html').read_text(), url_fetcher=data_only).render()
    if not 0 < len(document.pages) <= pages:
        raise ValueError('Page limit')
    document.write_pdf(folder / 'output.pdf')


if __name__ == '__main__':
    main()
