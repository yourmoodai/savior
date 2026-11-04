"""Assemble immutable release artifacts; no compilation occurs on user machines."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from macos_bundle import BUNDLE, RUNTIME, create as create_bundle

ROOT = Path(__file__).resolve().parents[2]
NODE = '24.22.2'
TARGETS = {
    'darwin-arm64': ('tar.gz', 'aa9549349e47dffaa42dcf95d6e06ade8c4bdafc980979883b88767670df385a', 'darwin-arm64'),
    'linux-x64': ('tar.gz', '361718c444e49af2ca5a110c83ba6a8901143f23ab285ebbef0341f391f482a9', 'linux-x64-gnu'),
    'win32-x64': ('zip', 'ca1bff5d8ead13c9f2ea404a176db46bd430704d5b5113734049fa365e7799e2', 'win32-x64-msvc'),
}
LIMIT = 210_000_000  # 210 MB, stricter than the host's 256 MiB download limit

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def copy_documentation(destination):
    for name in ['README.md', 'CONTRIBUTING.md']:
        shutil.copy2(ROOT/name, destination/name)
    shutil.copytree(ROOT/'docs', destination/'docs')
    shutil.copytree(ROOT/'benchmarks/results', destination/'benchmarks/results')
    for name in ['sources.json', 'NOTICE.md']:
        shutil.copy2(ROOT/'benchmarks'/name, destination/'benchmarks'/name)

def stage(target, destination):
    ext, expected, addon = TARGETS[target]
    suffix = '.exe' if target.startswith('win32') else ''
    directory = destination / 'runtime' / target
    directory.mkdir(parents=True, exist_ok=True)
    name = f'node-v{NODE}-{target.replace("win32", "win")}'
    archive = ROOT / '.cache' / f'{name}.{ext}'
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists() or digest(archive) != expected:
        urllib.request.urlretrieve(f'https://nodejs.org/dist/v{NODE}/{archive.name}', archive)
    if digest(archive) != expected:
        raise ValueError('node checksum mismatch')
    member = name + ('/node.exe' if suffix else '/bin/node')
    with (zipfile.ZipFile(archive) if suffix else tarfile.open(archive)) as bundle:
        for filename, output in [(member, 'node'+suffix),(name+'/LICENSE','NODE-LICENSE')]:
            source = bundle.open(filename) if suffix else bundle.extractfile(filename)
            with source, (directory/output).open('wb') as stream:
                shutil.copyfileobj(source,stream)
    for binary in ['savior','savior-review']:
        shutil.copy2(ROOT/'target/release'/(binary+suffix),directory/(binary+suffix))
    for name in ['node'+suffix,'savior'+suffix,'savior-review'+suffix]:
        (directory/name).chmod(0o755)
    (destination/'native').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'native'/f'savior.{addon}.node',destination/'native')
    for name in ['index.cjs','index.d.ts']:
        shutil.copy2(ROOT/'native'/name,destination/'native'/name)
    if target == 'darwin-arm64':
        shutil.copy2(ROOT/'target/release/savior-bootstrap', directory/'savior-bootstrap')
    provenance = {'schema': 1, 'target': target,
                  'version': json.loads((ROOT/'package.json').read_text())['version'],
                  'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'files': {p.relative_to(destination).as_posix(): digest(p) for p in sorted(destination.rglob('*')) if p.is_file() and p.name != 'stage.json'}}
    (destination/'stage.json').write_text(json.dumps(provenance, indent=2)+'\n')

def assemble(parts, destination, node='node', npm='npm', require_signed=False):
    if destination.exists():
        raise ValueError('output already exists; use a fresh staging directory')
    destination.mkdir(parents=True)
    version=json.loads((ROOT/'package.json').read_text())['version']
    for target in TARGETS:
        part=parts/target
        if not (part/'runtime'/target).is_dir(): raise ValueError('missing target '+target)
        shutil.copytree(part,destination,dirs_exist_ok=True,ignore=shutil.ignore_patterns('stage.json'))
        suffix='.exe' if target.startswith('win32') else ''
        for name in ['node','savior','savior-review']:
            (destination/'runtime'/target/(name+suffix)).chmod(0o755)
    for name in ['hooks','bridge','src','models','.claude-plugin']:
        shutil.copytree(ROOT/name,destination/name,ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['LICENSE','NOTICE','THIRD-PARTY-NOTICES.md','package.json','package-lock.json']:
        shutil.copy2(ROOT/name,destination/name)
    copy_documentation(destination)
    subprocess.run([npm,'ci','--omit=dev','--omit=optional','--ignore-scripts','--bin-links=false','--prefix',str(destination)],check=True)
    package=json.loads((destination/'package.json').read_text())
    for key in ['dependencies','devDependencies','scripts','napi','bin','files']:
        package.pop(key,None)
    (destination/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    (destination/'package-lock.json').unlink()
    (destination/'.claude-plugin/marketplace.json').unlink()
    (destination/'hooks/distribution.ts').write_text('export const packaged: boolean = true;\n')
    files={str(p.relative_to(destination)).replace('\\','/'):digest(p) for p in sorted(destination.rglob('*')) if p.is_file()}
    manifest={'version':version,'node':NODE,'targets':list(TARGETS),'files':files,'model':json.loads((ROOT/'models/catalog.json').read_text())['models'][0]}
    if not (destination/BUNDLE).exists():
        if require_signed:
            raise ValueError('signed macos bundle required')
        create_bundle(destination, version)
    if require_signed and not (destination/BUNDLE/'Contents/CodeResources').is_file():
        raise ValueError('stapled macos ticket required')
    runtime={'schema':2,'protocol':1,'version':version,'model':manifest['model'],'targets':{},
             'macos_bundle': []}
    for path in sorted((destination/BUNDLE).rglob('*')):
        if path.is_symlink(): raise ValueError('bundle contains symlink')
        if path.is_file():
            runtime['macos_bundle'].append({'path':path.relative_to(destination).as_posix(),
                                          'bytes':path.stat().st_size,'sha256':digest(path)})
    for target, (_, _, addon) in TARGETS.items():
        suffix='.exe' if target.startswith('win32') else ''
        entries=[]
        if target == 'darwin-arm64':
            for name in ['savior', 'savior-review', 'savior.node']:
                path = destination/RUNTIME/name
                entries.append({'name':name, 'path':RUNTIME+'/'+name,
                                'bytes':path.stat().st_size, 'sha256':digest(path)})
            runtime['targets'][target] = entries
            for name in ['node', 'savior', 'savior-review', 'savior-bootstrap']:
                (destination/'runtime'/target/name).unlink()
            (destination/'native'/f'savior.{addon}.node').unlink()
            continue
        for source, name in [(destination/'runtime'/target/('savior'+suffix),'savior'+suffix),
                             (destination/'runtime'/target/('savior-review'+suffix),'savior-review'+suffix),
                             (destination/'native'/f'savior.{addon}.node','savior.node')]:
            asset=f'runtime-{version}-{target}-{name}'
            entries.append({'name':name,'asset':asset,'bytes':source.stat().st_size,'sha256':digest(source)})
            shutil.copy2(source,destination.parent/asset)
            source.unlink()
        runtime['targets'][target]=entries
    (destination/'runtime-manifest.json').write_text(json.dumps(runtime,sort_keys=True,separators=(',',':'))+'\n')
    signing_env = dict(os.environ)
    if not signing_env.get('SAVIOR_RELEASE_SIGNING_KEY'):
        if require_signed or signing_env.get('GITHUB_REF', '').startswith('refs/tags/'):
            raise ValueError('production signing key required')
        signing_env['SAVIOR_RELEASE_SIGNING_KEY'] = subprocess.check_output([node, '-e',
            "process.stdout.write(require('crypto').generateKeyPairSync('ed25519').privateKey.export({type:'pkcs8',format:'pem'}))"], text=True)
    subprocess.run([node,str(ROOT/'scripts/release/sign.mjs'),str(destination/'runtime-manifest.json'),str(destination/'runtime-manifest.sig'),str(destination/'release-public.pem')],check=True,env=signing_env)
    for name in ['runtime-manifest.json','runtime-manifest.sig','release-public.pem']:
        shutil.copy2(destination/name,destination.parent/name)
    manifest['files']={str(p.relative_to(destination)).replace('\\','/'):digest(p) for p in sorted(destination.rglob('*')) if p.is_file()}
    (destination/'release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    output=destination.parent/f'savior-{version}.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(destination.rglob('*')):
            if path.is_symlink(): raise ValueError('release contains symlink')
            if path.is_file():archive.write(path,path.relative_to(destination))
    if output.stat().st_size>=LIMIT: raise ValueError('plugin archive exceeds 210 MB')
    marketplace=json.loads((ROOT/'.claude-plugin/marketplace.json').read_text())
    marketplace['plugins'][0]['source']={'source':'archive','url':f'https://github.com/yourmoodai/savior/releases/download/v{version}/{output.name}','sha256':digest(output)}
    (destination.parent/'marketplace.json').write_text(json.dumps(marketplace,indent=2)+'\n')
    (destination.parent/'SHA256SUMS').write_text(f'{digest(output)}  {output.name}\n')
    envelope = {'schema':1, 'version':version,
                'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                'assets':{p.name:digest(p) for p in sorted(destination.parent.iterdir()) if p.is_file()},
                'models':{('model-'+a['file']):a['sha256'] for a in manifest['model']['artifacts']}}
    envelope_path = destination.parent/'release-envelope.json'
    envelope_path.write_text(json.dumps(envelope, sort_keys=True)+'\n')
    subprocess.run([node,str(ROOT/'scripts/release/sign.mjs'),str(envelope_path),
                    str(destination.parent/'release-envelope.sig'),str(destination.parent/'release-public.pem')],
                   check=True,env=signing_env)
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':digest(output)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('stage');p.add_argument('target',choices=TARGETS);p.add_argument('destination',type=Path)
    p=sub.add_parser('assemble');p.add_argument('parts',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--node',default='node');p.add_argument('--npm',default='npm')
    p.add_argument('--require-signed',action='store_true')
    args=parser.parse_args()
    if args.command=='stage':stage(args.target,args.destination)
    else:assemble(args.parts,args.destination,args.node,args.npm,args.require_signed)
