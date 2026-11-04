import {createPrivateKey, createPublicKey} from 'node:crypto';
import {readFileSync} from 'node:fs';

try {
  const private_key = createPrivateKey(readFileSync(0));
  const expected = createPublicKey(readFileSync(process.argv[2]));
  if (private_key.asymmetricKeyType !== 'ed25519' || expected.asymmetricKeyType !== 'ed25519' ||
      !createPublicKey(private_key).export({type:'spki',format:'der'}).equals(expected.export({type:'spki',format:'der'}))) {
    throw Error('unexpected_signing_identity');
  }
} catch {
  process.stderr.write('invalid_manifest_signing_identity\n');
  process.exitCode = 1;
}
