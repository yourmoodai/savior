const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');

function validate(documents, dependabot, toolchain) {
  const coverage = new Set(dependabot.updates.filter(update => update['package-ecosystem'] === 'github-actions')
    .flatMap(update => update.directories || [update.directory]));
  if (!coverage.has('/')) throw Error('workflow update coverage missing');
  function check_steps(steps, composite, file) {
    for (const step of steps || []) {
      if (step.uses) {
        if (step.uses.startsWith('./')) {
          const directory = step.uses.slice(2);
          if (![directory+'/action.yml', directory+'/action.yaml'].some(name => documents[name])) {
            throw Error('local action missing: '+step.uses);
          }
        } else if (!/^[\w-]+\/[\w.-]+(?:\/[\w.-]+)*@[a-f0-9]{40}$/.test(step.uses)) {
          throw Error('action must use a full commit sha: '+step.uses);
        }
        if (step.uses.startsWith('dtolnay/rust-toolchain@') && step.with?.toolchain !== toolchain) {
          throw Error('rust toolchain differs from rust-toolchain.toml');
        }
        if (step.uses.startsWith('actions/checkout@') && step.with?.['persist-credentials'] !== false) {
          throw Error('checkout must disable credential persistence');
        }
      }
      if (composite && (Boolean(step.run) === Boolean(step.uses) ||
          (step.run && !step.shell) || 'timeout-minutes' in step)) {
        throw Error('invalid composite step: '+file);
      }
    }
  }
  for (const [file, document] of Object.entries(documents)) {
    if (document.runs) {
      if (!coverage.has('/'+path.posix.dirname(file))) throw Error('composite update coverage missing: '+file);
      if (document.runs.using !== 'composite' || !document.name || !document.description ||
          !Array.isArray(document.runs.steps)) throw Error('invalid composite metadata: '+file);
      check_steps(document.runs.steps, true, file);
    } else {
      for (const job of Object.values(document.jobs || {})) {
        if (job.uses && !/^[\w-]+\/[\w.-]+\/[^@]+@[a-f0-9]{40}$/.test(job.uses)) {
          throw Error('reusable workflow must use a full commit sha');
        }
        check_steps(job.steps, false, file);
      }
    }
  }
}

function main() {
  const root = path.resolve(__dirname, '../..');
  const documents = {};
  function visit(directory) {
    for (const entry of fs.readdirSync(path.join(root, directory), {withFileTypes:true})) {
      const file = directory+'/'+entry.name;
      if (entry.isDirectory()) visit(file);
      else if (/\.ya?ml$/.test(file)) documents[file] = yaml.load(fs.readFileSync(path.join(root, file), 'utf8'));
    }
  }
  visit('.github/workflows');
  visit('.github/actions');
  const dependabot = yaml.load(fs.readFileSync(path.join(root, '.github/dependabot.yml'), 'utf8'));
  const toolchain = fs.readFileSync(path.join(root, 'rust-toolchain.toml'), 'utf8').match(/^channel = "([^"]+)"/m)[1];
  validate(documents, dependabot, toolchain);
  console.log('workflow dependencies and local composites verified');
}

module.exports = {validate};
if (require.main === module) main();
