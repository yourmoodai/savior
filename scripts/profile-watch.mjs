import {spawn} from 'node:child_process';

if (!['darwin','linux'].includes(process.platform)) throw Error('watch_memory_profiling_requires_unix_time');
const child = spawn('/usr/bin/time', [process.platform === 'darwin' ? '-l' : '-v',
  'target/profiling/savior', 'watch', ...process.argv.slice(2)], {stdio:'inherit'});
const interrupt = () => {};
// The foreground process group receives Ctrl+C; let time print its measurements.
process.on('SIGINT', interrupt);
try {
  process.exitCode = await new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', code => resolve(code ?? 1));
  });
} finally {
  process.removeListener('SIGINT', interrupt);
}
