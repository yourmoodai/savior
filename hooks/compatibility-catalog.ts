// Hook modules can import code only; JSON imports are rejected by the host loader.
export const catalog = {
  schema: 1,
  adapters: [{
    id: 'composer_2_1_322',
    client_versions: ['2.1.322'],
    entry: 'hooks/register.tsx',
    typecheck: 'tsconfig.plugin.json',
    draft_recovery: 'host',
    types_revision: '3deb873687379f0c199a69fdaccc65f0865f7a29',
    types_sha256: 'e1c060069af2d494fc884e03f136ed0b3ddaa0eb5341e72e3acd712e5f637c49',
  }],
};
