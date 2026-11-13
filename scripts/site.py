"""Build the public site without application or model dependencies."""
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'
VERSION = '0.23.6'
ARCHIVES = {
    ('Darwin', 'arm64'): ('aarch64-apple-darwin.tar.gz', 'cbffbd29b3f59c3f52633507c8cb945a7a02d8b1399b43b235f5932912297aa3'),
    ('Darwin', 'x86_64'): ('x86_64-apple-darwin.tar.gz', '79a4d0ab51a4d863c068e6e594c6fce36f0aa17429a414ea63066f5910d14460'),
    ('Linux', 'x86_64'): ('x86_64-unknown-linux-gnu.tar.gz', '8f5132b3522412d04e395e0b25f6d68613ad272a873e54a2b3ebf664873024a4'),
    ('Linux', 'aarch64'): ('aarch64-unknown-linux-gnu.tar.gz', '266448fffbf7c7004ca399d0e76dd699541771096d8a42aede98cebe2a029d02'),
    ('Windows', 'AMD64'): ('x86_64-pc-windows-msvc.zip', '79179284f816febc281adbedb27189806cf22f83e8be4d0fca6835fbeeb09587'),
}


def zola():
    target, digest = ARCHIVES[(platform.system(), platform.machine())]
    directory = ROOT / '.cache' / 'zola' / VERSION / target
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / 'archive'
    if not archive.exists():
        with urllib.request.urlopen(f'https://github.com/getzola/zola/releases/download/v{VERSION}/zola-v{VERSION}-{target}', timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('zola checksum mismatch')
        archive.write_bytes(data)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('zola checksum mismatch; remove the corrupt cached archive')
    name = 'zola.exe' if platform.system() == 'Windows' else 'zola'
    binary = directory / name
    if target.endswith('.zip'):
        with zipfile.ZipFile(archive) as bundle:
            binary.write_bytes(bundle.read(name))
    else:
        with tarfile.open(archive) as bundle:
            binary.write_bytes(bundle.extractfile(name).read())
    binary.chmod(0o755)
    return str(binary)


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links = set(), []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'iframe', 'object', 'embed', 'form', 'base'}:
            raise ValueError(f'forbidden site element: {tag}')
        if any(key.startswith('on') or key in {'style', 'srcdoc'} for key in attrs):
            raise ValueError('active inline content')
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                raise ValueError('duplicate element id')
            self.ids.add(attrs['id'])
        for key in ('href', 'src'):
            if key in attrs:
                value = attrs[key]
                url = urlsplit(value)
                if url.scheme not in ('', 'https', 'http'):
                    raise ValueError('unexpected url scheme')
                if tag != 'a' and url.netloc and url.netloc != 'savior.yourmood.ai':
                    raise ValueError('external resource')
                self.links.append(value)


def check_output():
    output = SITE / 'public'
    pages = {path.relative_to(output).as_posix(): Page(path.read_text()) for path in output.rglob('*.html')}
    for name, page in pages.items():
        for value in page.links:
            url = urlsplit(value)
            if url.netloc and url.netloc != 'savior.yourmood.ai':
                continue
            path = unquote(url.path)
            target = output / path.lstrip('/') if path.startswith('/') else output / Path(name).parent / path
            if not path:
                target = output / name
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'missing local link: {name}: {value}')
            if url.fragment and target.suffix == '.html':
                if unquote(url.fragment) not in pages[target.relative_to(output).as_posix()].ids:
                    raise ValueError(f'missing anchor: {value}')
    css = (output / 'style.css').read_text()
    if '@import' in css or 'url(' in css or any(output.rglob('*.js')):
        raise ValueError('unexpected active or remote asset')
    print(f'site verified: {len(pages)} pages, local assets, no browser scripts')


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ''
    if command not in ('build', 'serve', 'check'):
        raise SystemExit('usage: python3 scripts/site.py build|serve|check')
    config = tomllib.loads((SITE / 'zola.toml').read_text())
    # Publishing a release claim requires a separately reviewed assurance decision.
    if config['extra']['release_status'] != 'unreleased':
        raise ValueError('release assurance has not been established')
    shutil.copyfile(ROOT / 'docs/assets/savior.svg', SITE / 'static/savior.svg')
    shutil.copyfile(ROOT / 'docs/assets/social-preview.png', SITE / 'static/social-preview.png')
    binary = zola()
    if command == 'serve':
        subprocess.run([binary, '--root', str(SITE), 'serve', '--interface', '127.0.0.1'], check=True)
    else:
        subprocess.run([binary, '--root', str(SITE), 'check', '--skip-external-links'], check=True)
        subprocess.run([binary, '--root', str(SITE), 'build'], check=True)
        check_output()


if __name__ == '__main__':
    main()
