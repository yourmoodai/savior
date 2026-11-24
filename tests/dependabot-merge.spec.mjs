import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as yaml from 'js-yaml';
import automation from '../tools/ci/dependabot.cjs';
import validation from '../tools/ci/pr_validation.cjs';
import invalidate from '../tools/ci/invalidate_validation.cjs';

const repository = {full_name: 'org/repo'};
const context = {repo: {owner: 'org', repo: 'repo'}, payload: {repository, workflow_run: {
  path: '.github/workflows/dependabot-build.yml', display_title: 'dependabot build #1',
  event: 'pull_request', conclusion: 'success', repository, head_repository: repository, head_sha: 'head'
}}};
const pr = {state: 'open', user: {type: 'Bot', login: 'dependabot[bot]'}, commits: 1,
  head: {repo: repository, sha: 'head', ref: 'dependabot/example'}, base: {ref: 'main', repo: repository, sha: 'base'},
  merge_commit_sha: 'test'};

test('only the repository validation workflow can initiate an update', () => {
  for (const patch of [{path: 'other'}, {event: 'push'}, {conclusion: 'skipped'},
    {conclusion: 'failure'}, {display_title: 'pr validation #1'}, {repository: {full_name: 'other'}}]) {
    assert.throws(() => automation.requested_pr({...context, payload: {...context.payload,
      workflow_run: {...context.payload.workflow_run, ...patch}}}));
  }
});

test('merge needs the current successful build and verification', () => {
  const workflow = yaml.load(readFileSync('.github/workflows/dependabot-build.yml', 'utf8'));
  assert.equal(workflow.jobs.build.needs, 'candidate');
  assert.equal(workflow.jobs.verify.needs, 'candidate');
  assert.ok(workflow.jobs.build.steps.some(step => step.run === 'npm run build'));
  assert.ok(workflow.jobs.build.steps.some(step => step.run === 'npm run verify'));
  assert.ok(Object.values(workflow.jobs.build.permissions).every(value => value === 'read'));
});

test('approval and merge target the tested signed bot revision', async () => {
  for (const scenario of ['success', 'regenerated', 'changed tree', 'human', 'fork', 'unsigned', 'skipped build', 'failed verification', 'stale head', 'stale base', 'changed after approval']) {
    const writes = [];
    const live = structuredClone(pr);
    if (scenario === 'human') live.user.login = 'contributor';
    if (scenario === 'fork') live.head.repo = {full_name: 'fork/repo'};
    if (scenario === 'stale head') live.head.sha = 'new';
    if (scenario === 'stale base') live.base.sha = 'new';
    const jobs = [
      {name: 'build base head tree', conclusion: scenario === 'skipped build' ? 'skipped' : 'success'},
      {name: 'verify / verification', conclusion: scenario === 'failed verification' ? 'failure' : 'success'}
    ];
    const github = {paginate: async endpoint => endpoint === 'jobs' ? jobs : [{author: pr.user, sha: live.head.sha,
      commit: {verification: {verified: scenario !== 'unsigned'}}}],
    rest: {pulls: {get: async () => ({data: live}), listCommits: 'commits',
      createReview: async args => {
        writes.push(['approve', args.commit_id]);
        if (scenario === 'changed after approval') live.head.sha = 'new';
      },
      merge: async args => {writes.push(['merge', args.sha]); return {data: {merged: true}};}
    }, git: {getRef: async () => ({data: {object: {sha: 'base'}}}), getCommit: async () => ({data: {sha: scenario === 'regenerated' ? 'other' : 'test',
      tree: {sha: scenario === 'changed tree' ? 'new' : 'tree'}, parents: [live.base, live.head]}})},
    actions: {listJobsForWorkflowRunAttempt: 'jobs', createWorkflowDispatch: async () => writes.push(['refresh'])}}};
    const result = automation.merge_run({github, context});
    if (['success', 'regenerated'].includes(scenario)) {
      await result;
      assert.deepEqual(writes, [['approve', 'head'], ['merge', 'head'], ['refresh']]);
    } else {
      await assert.rejects(result);
      assert.deepEqual(writes, scenario === 'changed after approval' ? [['approve', 'head']] : []);
    }
  }
});

test('bot updates skip reviewer admission and rebuild when main changes', async () => {
  const pull = {...pr, number: 1};
  const outputs = {};
  await validation.execute({phase: 'start', context: {...context, payload: {repository, pull_request: pull}},
    core: {setOutput: (name, value) => { outputs[name] = value; }},
    github: {rest: {pulls: {get: async () => ({data: pull})}}}});
  assert.deepEqual(outputs, {dependabot: 'true'});
  const dispatches = [];
  const updates = [];
  const github = {rest: {repos: {compareCommitsWithBasehead: async () => ({data: {ahead_by: 1}})},
    pulls: {list: 'pulls', updateBranch: async args => updates.push(args.expected_head_sha)}, actions: {listWorkflowRuns: 'runs',
    createWorkflowDispatch: async args => dispatches.push(args)}},
    paginate: async endpoint => endpoint === 'pulls' ? [pull] : []};
  await invalidate({github, context});
  assert.deepEqual(updates, ['head']);
  assert.equal(dispatches.length, 1);
  assert.equal(dispatches[0].workflow_id, 'dependabot-build.yml');
  assert.equal(dispatches[0].ref, pull.head.ref);
  assert.deepEqual(dispatches[0].inputs, {pr_number: '1'});
  await invalidate({github, context: {...context, payload: {repository, pull_request: pull,
    action: 'submitted', review: {state: 'approved'}}}});
  assert.equal(dispatches.length, 1);
});
