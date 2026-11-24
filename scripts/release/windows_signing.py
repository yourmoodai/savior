"""Build the Jsign command shared by production and the Windows canary."""


def signing_arguments(windows, store, password_file, paths):
    return ['--storetype', windows['storetype'], '--keystore', store,
            '--storepass', 'file:'+str(password_file), '--alias', windows['alias'],
            '--alg', 'SHA-256', '--tsmode', 'RFC3161', '--tsaurl', windows['timestamp_url'],
            *paths]
