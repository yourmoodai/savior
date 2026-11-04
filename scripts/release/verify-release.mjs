import {createHash, createPublicKey, verify} from 'node:crypto';
import {readFile, lstat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {join} from 'node:path';
import {verify_manifest} from '../../bridge/bootstrap.mjs';

export async function verify_release(directory, public_key, version, revision, with_models=false) {
  const key=createPublicKey(public_key);
  const bytes=await readFile(join(directory,'release-envelope.json'));
  const signature=await readFile(join(directory,'release-envelope.sig'));
  if(key.asymmetricKeyType!=='ed25519' || !verify(null,bytes,key,signature)) throw Error('invalid_release_signature');
  const envelope=JSON.parse(bytes);
  if(envelope.schema!==1 || envelope.version!==version || envelope.revision!==revision ||
     !/^[a-f0-9]{40}$/.test(revision)) throw Error('release_identity_mismatch');
  const runtime_bytes=await readFile(join(directory,'runtime-manifest.json'));
  const runtime_signature=await readFile(join(directory,'runtime-manifest.sig'));
  const expected=new Set(['runtime-manifest.json','runtime-manifest.sig','release-public.pem',
    'marketplace.json','SHA256SUMS',`savior-${version}.zip`]);
  let runtime;
  for(const target of ['darwin-arm64','linux-x64','win32-x64']) {
    runtime=verify_manifest(runtime_bytes,runtime_signature,public_key,version,target);
    for(const file of runtime.targets[target]) if(file.asset) expected.add(file.asset);
  }
  const assets=envelope.assets;
  if(!assets || Object.keys(assets).length!==expected.size || Object.keys(assets).some(name=>!expected.has(name))) throw Error('release_inventory_mismatch');
  const model=Object.fromEntries(runtime.model.artifacts.map(file=>['model-'+file.file,file.sha256]));
  if(JSON.stringify(Object.entries(envelope.models??{}).sort())!==JSON.stringify(Object.entries(model).sort())) throw Error('release_model_mismatch');
  const public_bytes=await readFile(join(directory,'release-public.pem'));
  if(!createPublicKey(public_bytes).export({format:'der',type:'spki'}).equals(key.export({format:'der',type:'spki'}))) throw Error('release_signer_mismatch');
  for(const [name,digest] of Object.entries({...assets,...(with_models?model:{})})) {
    if(!/^[a-zA-Z0-9._+-]+$/.test(name) || !/^[a-f0-9]{64}$/.test(digest)) throw Error('invalid_release_inventory');
    const path=join(directory,name), stat=await lstat(path);
    if(!stat.isFile() || stat.isSymbolicLink() || stat.nlink!==1) throw Error('unsafe_release_asset');
    const hash=createHash('sha256');
    for await(const chunk of createReadStream(path)) hash.update(chunk);
    if(hash.digest('hex')!==digest) throw Error('release_digest_mismatch');
  }
  return envelope;
}

if(import.meta.main) {
  try {
    const [directory,version,revision]=process.argv.slice(2);
    await verify_release(directory,process.env.SAVIOR_RELEASE_PUBLIC_KEY,version,revision,process.argv.includes('--models'));
    process.stdout.write('release envelope and assets verified\n');
  } catch {
    process.stderr.write('invalid_release_envelope\n');
    process.exitCode=1;
  }
}
