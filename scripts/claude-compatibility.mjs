import {spawnSync} from 'node:child_process';
import {select_adapter} from '../hooks/compatibility.ts';

const result = spawnSync(process.env.SAVIOR_CLAUDE_BINARY || 'claude', ['--version'], {encoding:'utf8',timeout:10000});
const version = result.status === 0 ? result.stdout.trim().match(/^(\d+\.\d+\.\d+) \(Claude Code\)$/)?.[1] : undefined;
const adapter = select_adapter(version);
console.log(JSON.stringify(adapter ? {status:'matched',client_version:version,adapter:adapter.id,entry:adapter.entry}
  : {status:'unsupported',reason:version ? 'client_not_qualified' : 'client_version_unavailable'}));
if (!adapter) process.exitCode = 1;
