import { packaged } from './distribution';

type Host = { plugin: {root: string}; process: {run(argv: readonly string[], options?: {timeoutMs?: number; env?: Record<string,string>}): Promise<{exitCode:number;stdout:string;stderr:string}>} };

async function linux_target(host: Host) {
  for (const uname of ['/usr/bin/uname', '/bin/uname', '/run/current-system/sw/bin/uname']) {
    try {
      const kernel = await host.process.run([uname, '-sm'], {timeoutMs:10000});
      if (kernel.exitCode !== 0) continue;
      switch (kernel.stdout.trim()) {
        case 'Linux aarch64': return 'linux-arm64';
        case 'Linux x86_64': return 'linux-x64';
        default: return 'unsupported';
      }
    } catch {}
  }
  return 'unsupported';
}

export class Runtime {
  node = 'node';
  env: Record<string,string> = {};
  ready: boolean;
  constructor(private readonly release = packaged) { this.ready = !release; }
  status = packaged ? 'preparing local filter' : 'developer runtime';
  async start(host: Host) {
    if (!this.release) return;
    this.ready = false;
    this.status = 'downloading and verifying local runtime and model; drafts remain local until ready';
    const targets = [['darwin-arm64','SaviorRuntime.app/Contents/MacOS/savior-bootstrap'], ['darwin-arm64','node'], ['linux-arm64','node'], ['linux-x64','node'], ['win32-x64','node.exe']] as const;
    try {
      let target: string | undefined;
      let linuxTarget: string | undefined;
      for (const [name, executable] of targets) {
        const node = host.plugin.root + '/runtime/' + name + '/' + executable;
        try {
          if (name.startsWith('linux-')) {
            linuxTarget ??= await linux_target(host);
            if (name !== linuxTarget) continue;
          }
          // First activation of the ARM64 launcher can download the pinned Node executable.
          const result = await host.process.run([node, '-p', 'process.platform+"-"+process.arch'], {timeoutMs:name === 'linux-arm64' ? 600000 : 10000});
          if (result.exitCode === 0 && result.stdout.trim() === name) { this.node = node; target = name; break; }
        } catch {}
        if (name === linuxTarget) break;
      }
      if (!target) { this.status = 'unsupported platform or runtime unavailable'; return; }
      const setup = await host.process.run([this.node, host.plugin.root + '/bridge/bootstrap.mjs'], {timeoutMs:600000});
      const prepared = JSON.parse(setup.stdout);
      if (setup.exitCode !== 0 || prepared.status !== 'ready' || typeof prepared.runtime_dir !== 'string' || !prepared.runtime_dir) throw Error('setup_failed');
      const binary = prepared.runtime_dir + '/savior' + (target === 'win32-x64' ? '.exe' : '');
      const result = await host.process.run([binary, 'prepare'], {timeoutMs:600000});
      const value = JSON.parse(result.stdout);
      if (result.exitCode !== 0 || value.status !== 'ready' || typeof value.model_dir !== 'string' || !value.model_dir) throw Error('setup_failed');
      this.env = {SAVIOR_MODEL_DIR:value.model_dir, SAVIOR_RUNTIME_DIR:prepared.runtime_dir, ...(typeof prepared.manifest_sha256==='string' ? {SAVIOR_RELEASE_MANIFEST_SHA256:prepared.manifest_sha256}: {})};
      const doctor = await host.process.run([this.node,host.plugin.root+'/bridge/cli.mjs','doctor'], {env:this.env,timeoutMs:45000});
      if (doctor.exitCode !== 0 || JSON.parse(doctor.stdout).status !== 'ready') throw Error('setup_failed');
      this.ready = true; this.status = 'local filter ready';
    } catch { this.status = 'setup incomplete; check network access and reopen /savior to retry'; }
  }
}
