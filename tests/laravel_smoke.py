#!/usr/bin/env python3
"""Serve the real fresh Laravel app in a disposable nested-source stack."""
import base64
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import uuid

from test_nested_source import NestedSourceTests


def main():
    fixture = NestedSourceTests()
    fixture.setUp()
    root = fixture.root
    prefix = 'laravel-smoke-' + uuid.uuid4().hex[:10]
    settings = root / 'docker/.env.production'
    settings.write_text(settings.read_text().replace('isolated-test', prefix))
    project, source = fixture.split()
    with (project / '.env').open('a') as stream:
        stream.write('WORKSPACE_SOURCE_MODE=mounted\n')
    app = Path(__file__).resolve().parents[1] / 'projects/itechorbit/src'
    if not (app / 'vendor/autoload.php').is_file():
        raise SystemExit('Run composer install in projects/itechorbit/src first.')
    shutil.copytree(app, source, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('node_modules', '.env', '.git', '*.log'))
    # Keep this test database-free at request time; infrastructure remains isolated.
    with (source / '.env').open('a') as stream:
        stream.write('\nAPP_KEY=base64:' + base64.b64encode(secrets.token_bytes(32)).decode() + '\n'
                     'SESSION_DRIVER=file\nCACHE_STORE=file\nQUEUE_CONNECTION=sync\nLOG_CHANNEL=single\n')
    fixture.fixture.sync('alpha')
    module = fixture.fixture.load_module()
    p = module.project('alpha')
    assert p['config'] == project / '.docker' and p['mounted']
    php_base = os.environ.get('WORKSPACE_SMOKE_PHP_BASE')
    nginx_base = os.environ.get('WORKSPACE_SMOKE_NGINX_BASE')
    if php_base:
        (p['config'] / 'Dockerfile').write_text(
            f'FROM {php_base} AS production\n'
            'LABEL io.php-workspace.runtime=mounted-production\n'
            'COPY production.ini /usr/local/etc/php/conf.d/zzz-production.ini\n'
            'COPY production-fpm.conf /usr/local/etc/php-fpm.d/zzz-production.conf\n'
            'COPY prepare-runtime /usr/local/bin/prepare-runtime\n')
    if nginx_base:
        (p['config'] / 'nginx.Dockerfile').write_text(
            f'FROM {nginx_base} AS production\n'
            'LABEL io.php-workspace.runtime=mounted-production\n'
            'COPY nginx.conf.template /etc/nginx/templates/default.conf.template\n')
    original = module.compose

    def compose(project, *args, **kwargs):
        kwargs['capture'] = True
        return original(project, *args, **kwargs)

    module.compose = compose

    def dc(*args):
        return compose(p, *args, profiles=False).stdout.strip()

    def request(path='/'):
        return dc('exec', '-T', 'php', 'curl', '--fail', '--silent', f'http://nginx{path}')

    try:
        print('Starting disposable nested Laravel stack...', flush=True)
        module.up(p)
        response = request()
        assert "Let's get started" in response
        before = dc('ps', '-q')
        with (source / 'routes/web.php').open('a') as stream:
            stream.write("\nRoute::get('/source-mount-check', fn () => 'live-source');\n")
        (source / 'public/source-mount-check.txt').write_text('live-design')
        # FPM's production OPcache rechecks source timestamps every two seconds.
        for _ in range(15):
            try:
                if request('/source-mount-check') == 'live-source':
                    break
            except subprocess.CalledProcessError as error:
                if error.returncode != 22:
                    raise
            time.sleep(1)
        assert request('/source-mount-check') == 'live-source'
        assert request('/source-mount-check.txt') == 'live-design'
        assert dc('ps', '-q') == before
        dc('exec', '-T', '-u', 'www-data', 'php', 'sh', '-ec',
           'test -w storage/logs; test -w bootstrap/cache; ! touch public/should-fail')
        assert (project / 'logs/php-error.log').exists()
        assert not (source / 'logs/php-error.log').exists()
        assert 'WORKSPACE_' not in (source / '.env').read_text()
        print('PASS: Laravel welcome page, live route/assets, split env, parent PHP logs, read-only source and writable runtime.', flush=True)
    finally:
        compose(p, 'down', '-v', '--remove-orphans', check=False, profiles=False)
        module.docker('network', 'rm', p['stack'] + '_edge', capture=True, check=False)
        for service in ('php', 'nginx'):
            module.docker('image', 'rm', f'{p["stack"]}-{service}:current', capture=True, check=False)
        fixture.doCleanups()


if __name__ == '__main__':
    main()
