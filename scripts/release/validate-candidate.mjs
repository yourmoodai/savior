import {readFile, readdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {verify_release} from './verify-release.mjs';

try {
  const [directory, expected_public_file] = process.argv.slice(2);
  const version = JSON.parse(await readFile(new URL('../../package.json', import.meta.url))).version;
  const revision = execFileSync('git', ['rev-parse','HEAD'], {encoding:'utf8'}).trim();
  const envelope = await verify_release(directory, await readFile(expected_public_file), version, revision);
  const expected = new Set([...Object.keys(envelope.assets),'release-envelope.json','release-envelope.sig']);
  const actual = (await readdir(directory)).filter(name => name !== 'plugin');
  if(actual.length !== expected.size || actual.some(name => !expected.has(name))) throw Error('unexpected_candidate_inventory');
  process.stdout.write('candidate signatures and artifact hashes verified\n');
} catch {
  process.stderr.write('invalid_release_candidate\n');
  process.exitCode = 1;
}
