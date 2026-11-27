import {spawn} from 'node:child_process';

if (!['darwin','linux'].includes(process.platform)) throw Error('network_memory_profiling_requires_unix_time');
if (process.argv.length !== 3) throw Error('network_profile_requires_configuration_directory');
const child = spawn('/usr/bin/time', [process.platform === 'darwin' ? '-l' : '-v',
  'target/profiling/savior-network', 'serve', process.argv[2]], {stdio:'inherit'});
// The foreground group receives Ctrl+C; time must survive to report peak memory.
const interrupt = () => {};
process.on('SIGINT', interrupt);
try {
  process.exitCode = await new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', code => resolve(code ?? 1));
  });
} finally {
  process.removeListener('SIGINT', interrupt);
}
