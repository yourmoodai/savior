"""Assemble immutable release artifacts; no compilation occurs on user machines."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from macos_bundle import BUNDLE, RUNTIME, create as create_bundle
from version_gate import development_version

ROOT = Path(__file__).resolve().parents[2]
NODE = '26.13.1'
ARM64_NODE_PIN = Path(__file__).resolve().with_name('node-linux-arm64.sha256')
TARGETS = {
    'darwin-arm64': ('tar.gz', 'aec2514ea1ceb2ba52455e9ec65986f6749a06dd759e5db2a72e6889668c9735', 'darwin-arm64'),
    'linux-x64': ('tar.gz', '518674de2b621728a5e4486f5fb3b7d2900bb3cc4aa97969edaa463db99f9778', 'linux-x64-gnu'),
    'linux-arm64': ('tar.gz', '80a330b35415382878df18d41afbd106ea89bfb3be0087d6608e289a14c5cc7d', 'linux-arm64-gnu'),
    'win32-x64': ('zip', 'e2edda75bfb089654705cf65a13b58933bfd5f57148e8ebda916f58a2474f1f7', 'win32-x64-msvc'),
}
LIMIT = 210_000_000  # 210 MB, stricter than the host's 256 MiB download limit

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def arm64_node_digest(path):
    actual = digest(path)
    if actual != ARM64_NODE_PIN.read_text().strip():
        raise ValueError('ARM64 Node does not match the launcher pin')
    return actual

def copy_documentation(destination):
    for name in ['README.md', 'CONTRIBUTING.md']:
        shutil.copy2(ROOT/name, destination/name)
    shutil.copytree(ROOT/'docs', destination/'docs')
    shutil.copytree(ROOT/'benchmarks/results', destination/'benchmarks/results')
    for name in ['sources.json', 'NOTICE.md']:
        shutil.copy2(ROOT/'benchmarks'/name, destination/'benchmarks'/name)

def verify_linux_architecture(part, target):
    if target not in ('linux-x64', 'linux-arm64'):
        return
    machine = 62 if target == 'linux-x64' else 183
    runtime = part/'runtime'/target
    binaries = [runtime/name for name in ('node', 'savior', 'savior-review')]
    binaries.append(part/'native'/f'savior.{TARGETS[target][2]}.node')
    if target == 'linux-arm64':
        binaries.append(runtime/'savior-node-launcher')
    for binary in binaries:
        with binary.open('rb') as stream:
            header = stream.read(64)
        if (len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01' or
                int.from_bytes(header[18:20], 'little') != machine):
            raise ValueError(f'invalid ELF architecture for {target}: {binary.name}')


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
    if target == 'linux-arm64':
        arm64_node_digest(directory/'node')
        shutil.copy2(ROOT/'target/release/savior-node-launcher', directory/'savior-node-launcher')
    verify_linux_architecture(destination, target)
    provenance = {'schema': 1, 'target': target,
                  'version': json.loads((ROOT/'package.json').read_text())['version'],
                  'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'files': {p.relative_to(destination).as_posix(): digest(p) for p in sorted(destination.rglob('*')) if p.is_file() and p.name != 'stage.json'}}
    (destination/'stage.json').write_text(json.dumps(provenance, indent=2)+'\n')

def assemble(parts, destination, node='node', npm='npm', require_signed=False, development=False,
             windows_signers=None, windows_record=None):
    version=json.loads((ROOT/'package.json').read_text())['version']
    ref=os.environ.get('GITHUB_REF', '')
    if windows_signers is not None and (
            not require_signed or not isinstance(windows_signers, dict)
            or set(windows_signers) != {'savior.exe', 'savior-review.exe', 'savior.node'}
            or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in windows_signers.values())):
        raise ValueError('invalid_windows_signer_inventory')
    if development and (require_signed or not development_version(version)
                        or ref != 'refs/tags/v'+version):
        raise ValueError('development packaging requires matching development tag')
    if destination.exists():
        raise ValueError('output already exists; use a fresh staging directory')
    destination.mkdir(parents=True)
    version=json.loads((ROOT/'package.json').read_text())['version']
    for target in TARGETS:
        part=parts/target
        if not (part/'runtime'/target).is_dir(): raise ValueError('missing target '+target)
        verify_linux_architecture(part, target)
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
    if windows_record is not None:
        if not require_signed or windows_signers is None:
            raise ValueError('verified_windows_stage_requires_signed_assembly')
        from windows_handoff import verify
        evidence = verify(parts/'win32-x64', windows_record, os.environ['WINDOWS_RECORD_SHA256'], 'release')
        manifest['windows_verification'] = {'artifact_id':os.environ['WINDOWS_ARTIFACT_ID'],
                                           'record_sha256':os.environ['WINDOWS_RECORD_SHA256'],
                                           'record':evidence}
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
            if target == 'win32-x64' and windows_signers is not None:
                entries[-1]['signer_sha256'] = windows_signers[name]
            shutil.copy2(source,destination.parent/asset)
            source.unlink()
        if target == 'linux-arm64':
            directory = destination/'runtime'/target
            node_binary = directory/'node'
            asset = f'runtime-{version}-{target}-node'
            entries.append({'name':'node', 'asset':asset, 'bytes':node_binary.stat().st_size,
                            'sha256':arm64_node_digest(node_binary)})
            node_binary.replace(destination.parent/asset)
            (directory/'savior-node-launcher').replace(node_binary)
            node_binary.chmod(0o755)
        runtime['targets'][target]=entries
    (destination/'runtime-manifest.json').write_text(json.dumps(runtime,sort_keys=True,separators=(',',':'))+'\n')
    signing_env = dict(os.environ)
    if not signing_env.get('SAVIOR_RELEASE_SIGNING_KEY'):
        if require_signed or (ref.startswith('refs/tags/') and not development):
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
    if development_version(version):
        marketplace['name']='yourmood.ai-dev'
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

def package_command(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('stage');p.add_argument('target',choices=TARGETS);p.add_argument('destination',type=Path)
    p=sub.add_parser('assemble');p.add_argument('parts',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--node',default='node');p.add_argument('--npm',default='npm')
    p.add_argument('--require-signed',action='store_true')
    p.add_argument('--development',action='store_true')
    p.add_argument('--windows-signers',type=json.loads)
    p.add_argument('--windows-verification-record',type=Path)
    args=parser.parse_args(argv)
    if args.command=='stage':stage(args.target,args.destination)
    else:assemble(args.parts,args.destination,args.node,args.npm,args.require_signed,args.development,
                  args.windows_signers,args.windows_verification_record)

if __name__=='__main__':
    package_command()
