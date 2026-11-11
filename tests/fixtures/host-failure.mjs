// Installed only in a disposable fixture; the candidate CLI remains beside it.
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const control = new URL('./host-fault.json', import.meta.url);
const fault = existsSync(control) ? JSON.parse(readFileSync(control, 'utf8')).scenario : null;
const command = process.argv[2];
if (fault === 'startup_failure' && command === 'doctor') {
  process.exitCode = 73;
} else if (command !== 'rewrite' || !fault || fault === 'startup_failure') {
  await import('./cli.real.mjs');
} else if (fault === 'missing_model') {
  process.env.SAVIOR_MODEL_DIR = fileURLToPath(new URL('./absent-model', import.meta.url));
  await import('./cli.real.mjs');
} else {
  for await (const chunk of process.stdin) { void chunk; }
  if (fault === 'worker_exit') process.exitCode = 73;
  else if (fault === 'malformed_output') process.stdout.write('{');
  else if (fault === 'processing_timeout') {
    process.stdout.write(JSON.stringify({version:1,status:'error',code:'processing_timeout'})+'\n');
    process.exitCode = 1;
  } else if (fault === 'worker_deadline') {
    writeFileSync(new URL('./host-worker.pid', import.meta.url), String(process.pid));
    await new Promise(() => { setInterval(() => {}, 1000); });
  } else throw Error('invalid_test_scenario');
}
