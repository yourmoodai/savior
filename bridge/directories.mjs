import {isAbsolute, join} from 'node:path';
import {lstat} from 'node:fs/promises';
import {createHash} from 'node:crypto';

export function cache_root(env=process.env, platform=process.platform) {
  if(platform==='win32') {
    if(!env.LOCALAPPDATA || !isAbsolute(env.LOCALAPPDATA)) throw Error('invalid_cache_path');
    return env.LOCALAPPDATA;
  }
  if(env.XDG_CACHE_HOME && isAbsolute(env.XDG_CACHE_HOME)) return env.XDG_CACHE_HOME;
  if(!env.HOME || !isAbsolute(env.HOME)) throw Error('invalid_cache_path');
  return join(env.HOME,'.cache');
}

export async function socket_directory(profile, env=process.env) {
  const uid=process.geteuid();
  const name='savior-'+uid+'-'+createHash('sha256').update(profile).digest('hex');
  const runtime=env.XDG_RUNTIME_DIR;
  if(runtime && isAbsolute(runtime)) {
    const metadata=await lstat(runtime);
    if(!metadata.isDirectory() || metadata.uid!==uid || (metadata.mode&0o777)!==0o700) throw Error('invalid_runtime_path');
    // Preserve filesystem traversal and byte length exactly as Rust's Path::join.
    const directory=runtime+(runtime.endsWith('/')?'':'/')+name;
    // Reserve space for /watch and the NUL within Darwin's socket limit.
    if(Buffer.byteLength(directory)+6<=100) return directory;
  }
  return join('/tmp',name);
}
