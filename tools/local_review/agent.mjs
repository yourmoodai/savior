import {spawnSync} from 'node:child_process';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir,homedir} from 'node:os';
import {join} from 'node:path';
import receipt from './receipt.cjs';

const deny = ['command','read_file','write_file','read_url','execute_url','download_url','mcp'].map(tool=>tool+'(*)');
const settings = {allowNonWorkspaceAccess:false,toolPermission:'strict',useG1Credits:false,permissions:{deny}};
const strings = fields => Object.fromEntries(fields.map(key=>[key,{type:'string'}]));
const rows = properties => ({type:'array',items:{type:'object',properties,required:Object.keys(properties)}});
const schema = {type:'object', properties:{
  request:rows(strings(['path','side'])),
  verdict:{type:'string',enum:['approve','changes_requested','incomplete']},
  coverage:rows(strings(['path','reason'])),
  prior:rows({id:{type:'integer'},...strings(['status','reason'])}),
  findings:rows({line:{type:'integer'},prior_ids:{type:'array',items:{type:'integer'}},...strings(['path','side','evidence','reason'])}),
}, additionalProperties:false};
const commands = {
  agy:['--sandbox','--disable-slash-commands','--settings',JSON.stringify(settings),'--json-schema',JSON.stringify(schema),'--print-timeout','12m'],
  claude:['--safe-mode','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','','--no-session-persistence','--print'],
  codex:['-a','never','exec','--sandbox','read-only','--ignore-user-config','--ignore-rules','--ephemeral',
    '--skip-git-repo-check','--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
    '-c','web_search="disabled"'],
};

function read_settings(path) {
  try { return readFileSync(path,'utf8'); }
  catch (error) { if (error.code==='ENOENT') return ''; throw error; }
}

function parse_output(text) {
  try { return JSON.parse(text); }
  catch { throw Error('malformed reviewer output'); }
}

export function agent(name='agy',model=process.env.SAVIOR_REVIEW_MODEL) {
  if (!Object.hasOwn(commands,name)) throw Error('choose agy, claude or codex');
  const args = [...commands[name]];
  const env = {};
  for (const key of ['PATH','HOME','USER','LANG','TMPDIR','DEVELOPER_PROFILE','PROFILE_ROUTER_ACTIVE',
    'AGY_CONFIG_DIR','CLAUDE_CONFIG_DIR','CODEX_CONFIG_DIR','CODEX_HOME'])
    if (process.env[key]) env[key]=process.env[key];
  const config_path = name==='agy' ? join(homedir(),'.gemini','antigravity-cli','settings.json') :
    name==='claude' ? join(env.CLAUDE_CONFIG_DIR || join(homedir(),'.claude'),'settings.json') :
    join(env.CODEX_HOME || join(homedir(),'.codex'),'config.toml');
  const config=read_settings(config_path);
  const routed=name==='agy' && env.AGY_CONFIG_DIR ? join(env.AGY_CONFIG_DIR,'settings.json') : null;
  const profile_settings=routed ? [routed,read_settings(routed)] : [];
  if (name==='claude' && process.env.CLAUDE_CODE_OAUTH_TOKEN) env.CLAUDE_CODE_OAUTH_TOKEN=process.env.CLAUDE_CODE_OAUTH_TOKEN;
  if (name==='codex' && !model) model=/^model\s*=\s*"([^"]+)"/m.exec(config)?.[1];
  if (model) args.push('--model',model);
  if (name==='agy') args.push('--input-format','stream-json','--output-format','stream-json');
  if (name==='codex') args.push('-');
  const version = spawnSync(name,['--version'],{env,encoding:'utf8',timeout:15_000});
  if (version.status!==0) throw Error('cannot identify installed '+name);
  const identity = receipt.sha256(JSON.stringify([name,version.stdout.trim(),model,args,config_path,config,profile_settings,env.DEVELOPER_PROFILE,env.PATH]));
  function infer(input) {
    const cwd = mkdtempSync(join(tmpdir(),'savior-review-'));
    try {
      if (name==='agy') input=JSON.stringify({event:'user',message:{content:[{type:'text',text:input}]}})+'\n';
      const result = spawnSync(name,args,{cwd,env,input,encoding:'utf8',timeout:750_000,maxBuffer:2_000_000});
      if (result.error || result.status!==0) {
        if (/failed to authenticate|authentication required/i.test(result.stdout+' '+result.stderr))
          throw Error(name+' requires sign-in for the configured profile');
        throw Error(name+' review failed ('+(result.error?.code || result.status)+')');
      }
      let output = result.stdout;
      if (name==='agy') {
        const events = output.trim().split('\n').map(parse_output);
        const final = events.filter(e=>e.event==='result');
        if (final.length!==1 || final[0].result.status!=='SUCCESS') throw Error('agy did not complete the review');
        const structured = final[0].result.structured_output;
        if (!structured || typeof structured!=='object') throw Error('agy returned no structured assessment');
        return structured;
      }
      const raw = output.trim().replace(/^\x60\x60\x60(?:json)?\s*\n([\s\S]*?)\n\x60\x60\x60$/,'$1');
      return parse_output(raw);
    } finally { rmSync(cwd,{recursive:true,force:true}); }
  }
  return {name,identity,infer};
}
