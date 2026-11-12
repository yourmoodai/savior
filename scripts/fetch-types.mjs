import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {catalog} from '../hooks/compatibility-catalog.ts';
const client = process.env.SAVIOR_HOST_VERSION || JSON.parse(await readFile(new URL('../tools/host/package.json', import.meta.url))).dependencies['@anthropic-ai/claude-code'];
const matches = catalog.adapters.filter(adapter => adapter.client_versions.includes(client));
if (matches.length !== 1) throw Error('client API contract missing or ambiguous');
const {id, types_sha256: digest, types_revision: revision} = matches[0];
if (!/^[a-z][a-z0-9_]*$/.test(id)) throw Error('invalid adapter identity');
const path = new URL(`../.cache/host-types/${id}/claude-code.d.ts`, import.meta.url);
if (!/^[a-f0-9]{40}$/.test(revision) || !/^[a-f0-9]{64}$/.test(digest)) throw Error('invalid type identity');
let bytes;
try { bytes = await readFile(path); } catch {}
if (!bytes || createHash('sha256').update(bytes).digest('hex') !== digest) {
  const response = await fetch(`https://raw.githubusercontent.com/anthropics/claude-code/${revision}/mods/types/claude-code.d.ts`);
  if (!response.ok) throw new Error('type download failed');
  bytes = Buffer.from(await response.arrayBuffer());
  if (createHash('sha256').update(bytes).digest('hex') !== digest) throw new Error('type digest mismatch');
  await mkdir(new URL('.', path), { recursive: true });
  await writeFile(path, bytes);
}
console.log('host type declarations verified');
