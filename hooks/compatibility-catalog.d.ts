export interface AdapterContract {
  id: string;
  client_versions: string[];
  entry: string;
  typecheck: string;
  draft_recovery: 'host';
  types_revision: string;
  types_sha256: string;
}

export declare const catalog: {
  schema: 1;
  adapters: AdapterContract[];
};
