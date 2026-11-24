"""Expected Authenticode publisher identity for release and native checks."""
import re


def profile_eku(config):
    identities = config['identities']
    if 'windows_profile_eku' not in identities:
        return None
    oid = identities['windows_profile_eku']
    if (config.get('windows', {}).get('storetype') != 'TRUSTEDSIGNING'
            or 'windows' in identities or not isinstance(oid, str)
            or not re.fullmatch(r'1\.3\.6\.1\.4\.1\.311\.97(?:\.(?:0|[1-9][0-9]{0,9})){4}', oid)):
        raise ValueError('invalid_windows_profile_identity')
    return oid
