// Hook modules can import code only; JSON imports are rejected by the host loader.
export const catalog = {
  schema: 1,
  adapters: [{
    id: 'composer_2_1_322',
    client_versions: ['2.1.322', '2.1.330'],
    entry: 'hooks/register.tsx',
    typecheck: 'tsconfig.plugin.json',
    draft_recovery: 'host',
    types_revision: 'e791a1c41eee7504672a109fd6eb66b11566b8a1',
    types_sha256: '7afdda2e6a7a40e2e8d12e77ab98129df6e45f460545da9cfec1fda783d488f0',
  }],
};
