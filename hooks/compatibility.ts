import {catalog} from './compatibility-catalog.ts';

export function select_adapter(version: unknown, adapters = catalog.adapters) {
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+$/.test(version)) return null;
  const matches = adapters.filter(adapter => adapter.client_versions.includes(version));
  return matches.length === 1 ? matches[0]! : null;
}

export class ClientCompatibility {
  status = 'client compatibility unverified; supported submissions are blocked';
  private pending: Promise<boolean> | undefined;

  reset() { this.pending = undefined; }

  check(version: () => Promise<{version: string}>) {
    // A session keeps its selected API contract. Unknown clients never fall
    // through to a nearby version or a different adapter's hooks.
    this.pending ??= (async () => {
      try {
        const adapter = select_adapter((await version()).version);
        if (adapter?.id === 'composer_2_1_322') {
          this.status = 'claude client version matched';
          return true;
        }
      } catch {}
      this.status = 'unsupported claude client; use a qualified Savior adapter before submitting';
      return false;
    })();
    return this.pending;
  }
}
