import {execFileSync} from 'node:child_process';
import MarkdownIt from 'markdown-it';

export const policy_version = 'prose-v1';
const guides = new Set(['README.md', 'docs/usage.md', 'docs/inspection.md',
  'docs/evaluation.md', 'docs/benchmarking.md', 'docs/migration.md']);
const spelling = new Map([['teh', 'the'], ['recieve', 'receive'], ['occured', 'occurred'],
  ['seperate', 'separate'], ['suported', 'supported'], ['replacment', 'replacement']]);
const markdown = new MarkdownIt({html: true, linkify: true});
const sha = /^[a-f0-9]{40}$/;
const max_bytes = 262144; // 256 KiB per source document
const instructions = /\b(run|execute|install|sudo|shell|npm|git|must|should|command|ignore|instructions?|workflow|policy|generated)\b/i;

function document_parts(text) {
  if (/[\0\r\u2028\u2029]/.test(text) || !text.endsWith('\n')) return null;
  if (/^---(?:\n|$)|<!--|\{\{|^:::|^\+\+\+$|@generated|do not edit/im.test(text)) return null;
  if (text.split('\n').some(line => /<\/?[a-z!]/i.test(line) &&
      !/^<img src="docs\/assets\/savior\.png" width="180" alt="Savior golden sparkle">$/.test(line))) return null;
  const lines = text.split('\n');
  const tokens = markdown.parse(text, {});
  const parts = [];
  let cursor = 0;
  for (const [index, token] of tokens.entries()) {
    if (token.type !== 'paragraph_open' || token.level !== 0) continue;
    const [start, end] = token.map;
    const inline = tokens[index + 1];
    const body = lines.slice(start, end);
    if (inline.type !== 'inline' || inline.children.some(t => !['text', 'softbreak'].includes(t.type))) continue;
    if (!body.every(line => /^[A-Za-z][A-Za-z ,.'-]*[A-Za-z.]$/.test(line))) continue;
    const words = body.join(' ');
    if (!/^(Savior|The|This|These|It) /.test(words) || !words.endsWith('.') || instructions.test(words)) continue;
    parts.push({before: JSON.stringify(lines.slice(cursor, start)), words});
    cursor = end;
  }
  return {parts, after: JSON.stringify(lines.slice(cursor))};
}

export function prose_edit(before, after) {
  const old = document_parts(before), head = document_parts(after);
  if (!old || !head || before === after || old.after !== head.after || old.parts.length !== head.parts.length) return false;
  return old.parts.every((part, i) => {
    const changed = head.parts[i];
    const words = part.words.split(' '), next = changed.words.split(' ');
    return part.before === changed.before && words.length === next.length &&
      words.every((word, n) => word === next[n] ||
        word.replace(/^[A-Za-z]+/, value => spelling.get(value) ?? value) === next[n]);
  });
}

export function git(repo, args, options = {}) {
  return execFileSync('git', ['--no-replace-objects', '-C', repo, ...args], {
    timeout: 60000, maxBuffer: 2097152, stdio: ['ignore', 'pipe', 'pipe'], ...options
  }); // 2 MiB maximum command output
}

export function eligible(repo, base, head, expected_files) {
  if (!sha.test(base) || !sha.test(head) || !Number.isSafeInteger(expected_files) ||
      expected_files < 1 || expected_files > guides.size) return false;
  if (git(repo, ['merge-base', base, head]).toString().trim() !== base) return false;
  const raw = git(repo, ['diff-tree', '-r', '--no-commit-id', '--no-renames', '--no-abbrev', '--raw', '-z', base, head]);
  const records = new TextDecoder('utf-8', {fatal: true}).decode(raw).split('\0');
  if (records.pop() !== '' || records.length !== expected_files * 2) return false;
  function blob(oid) {
    if (Number(git(repo, ['cat-file', '-s', oid])) > max_bytes) throw Error('document exceeds policy limit');
    return new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(git(repo, ['cat-file', 'blob', oid]));
  }
  for (let i = 0; i < records.length; i += 2) {
    const entry = /^:100644 100644 ([a-f0-9]{40}) ([a-f0-9]{40}) M$/.exec(records[i]);
    if (!entry || !guides.has(records[i+1]) || !prose_edit(blob(entry[1]), blob(entry[2]))) return false;
  }
  return true;
}
