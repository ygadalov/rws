#!/usr/bin/env python3
"""Build standalone index.html from dashboard/landing-template.html."""
import base64
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = 'https://ygadalov.github.io/rws/'


def data_uri(path):
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def main():
    html = (ROOT / 'dashboard' / 'landing-template.html').read_text(encoding='utf-8')

    def stylesheet(match):
        css = (ROOT / match.group(1)).read_text(encoding='utf-8')
        return '<style>\n' + css + '\n</style>'

    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', stylesheet, html)

    def resource(match):
        return match.group(1) + data_uri(ROOT / match.group(2)) + '"'

    html = re.sub(r'(src=")([^":]+)"', resource, html)
    html = re.sub(r'(<link rel="icon" href=")([^":]+)"', resource, html)
    html = html.replace('href="./"', 'href="#main"')
    # The application remains a separate download; portable landing links to the site.
    html = re.sub(r'href="(dashboard/[^"#]+)"', lambda m: 'href="' + SITE + m.group(1) + '"', html)
    html = re.sub(r'href="(data/samples/[^"]+)"', lambda m: 'href="' + data_uri(ROOT / m.group(1)) + '"', html)
    out = ROOT / 'index.html'
    out.write_text(html, encoding='utf-8')
    print(f'{out.name}: {out.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
