import {execFileSync} from 'node:child_process';
import {createSign} from 'node:crypto';

import receipt from './receipt.cjs';
const {repository, repo_id, app_id} = receipt;
const permissions = {contents:'read', pull_requests:'write', checks:'write'};

async function request(token, path, method = 'GET', body) {
  const response = await fetch('https://api.github.com'+path, {
    method, redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: {Authorization:'Bearer '+token, Accept:'application/vnd.github+json',
      'Content-Type':'application/json', 'User-Agent':'savior-local-review'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) throw Error('github '+method+' failed with '+response.status);
  return response.status === 204 ? null : response.json();
}

export async function app_client(write = false) {
  const key = execFileSync('/usr/bin/security', ['find-generic-password','-a',String(app_id),
    '-s','ai.yourmood.savior.review','-w'], {stdio:['ignore','pipe','pipe'],timeout:10000}).toString().trim();
  const now = Math.floor(Date.now()/1000);
  const encode = object => Buffer.from(JSON.stringify(object)).toString('base64url');
  const claims = encode({alg:'RS256',typ:'JWT'})+'.'+encode({iat:now-60,exp:now+480,iss:app_id});
  const signature = createSign('RSA-SHA256').update(claims).sign(Buffer.from(key,'base64'),'base64url');
  const jwt = claims+'.'+signature;
  const installation = await request(jwt, '/repos/'+repository+'/installation');
  if (installation.app_id !== app_id || installation.account.login !== repository.split('/')[0] ||
      installation.repository_selection !== 'selected' || installation.suspended_at) throw Error('unexpected installation');
  const grant = await request(jwt, '/app/installations/'+installation.id+'/access_tokens', 'POST',
    {repository_ids:[repo_id], permissions:write ? permissions : {contents:'read',pull_requests:'read',checks:'read'}});
  if (grant.repositories?.length !== 1 || grant.repositories[0].id !== repo_id) throw Error('unexpected token scope');
  return (suffix, method, body) => request(grant.token,'/repos/'+repository+suffix,method,body);
}
