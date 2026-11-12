function matcher(pattern) {
  const directory = pattern.endsWith('/');
  const rooted = pattern.startsWith('/') || pattern.slice(0,-1).includes('/');
  pattern = pattern.replace(/^\//,'').replace(/\/$/,'');
  let expression = rooted ? '^' : '(?:^|/)';
  for (let i=0;i<pattern.length;i++) {
    const char=pattern[i];
    if (char==='*' && pattern[i+1]==='*') {
      i++;
      if (pattern[i+1]==='/') {expression+='(?:[^/]+/)*';i++;}
      else expression+='.*';
    } else if (char==='*') expression+='[^/]*';
    else if (char==='?') expression+='[^/]';
    else {
      const literal=char==='\\' && i+1<pattern.length ? pattern[++i] : char;
      expression+=literal.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    }
  }
  expression += directory || !/[*?]/.test(pattern.split('/').at(-1)) ? '(?:/.*)?$' : '$';
  return new RegExp(expression);
}

function applicable(source, files) {
  // Last-match precedence includes entries that intentionally have no owners.
  const entries = source.split(/\r?\n/).map(line=>line.replace(/(?<!\\)#.*$/, '').trim())
    .filter(line=>line && !/^[!#]|\\#|[\[\]]/.test(line))
    .map(line=>line.split(/(?<!\\)\s+/));
  const rules = entries.map(([pattern,...owners])=>({match:matcher(pattern),owners}));
  const owners = new Set();
  for (const file of files) {
    const rule = rules.findLast(rule=>rule.match.test(file));
    for (const owner of rule?.owners || []) {
      if (/^@[a-zA-Z0-9-]+(?:\/[a-zA-Z0-9_.-]+)?$/.test(owner)) owners.add(owner);
    }
  }
  return [...owners].sort();
}

async function owners({github,context,pr}) {
  let source;
  for (const name of ['.github/CODEOWNERS','CODEOWNERS','docs/CODEOWNERS']) {
    try {
      const {data} = await github.rest.repos.getContent({...context.repo,path:name,ref:pr.base.sha});
      if (data.type !== 'file' || data.encoding !== 'base64' || data.size >= 3_000_000) throw Error('invalid codeowners file'); // 3 MB GitHub limit
      source = Buffer.from(data.content,'base64').toString();
      break;
    } catch (error) { if (error.status !== 404) throw error; }
  }
  if (source === undefined) return [];
  const files = await github.paginate(github.rest.pulls.listFiles,{...context.repo,pull_number:pr.number,per_page:100});
  if (files.length !== pr.changed_files) throw Error('incomplete codeowner file coverage');
  return applicable(source,files.flatMap(f=>[f.filename,...(f.previous_filename ? [f.previous_filename] : [])]));
}

module.exports = {applicable,owners};
