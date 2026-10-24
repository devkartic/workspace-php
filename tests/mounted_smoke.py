#!/usr/bin/env python3
"""Disposable mounted-source integration test; no public ports or existing data."""
import json
import os
import shutil
import time
import uuid

from test_production import ProductionTests


def main():
    fixture = ProductionTests()
    fixture.setUp()
    root = fixture.root
    prefix = 'mounted-smoke-' + uuid.uuid4().hex[:10]
    settings = root / 'docker/.env.production'
    settings.write_text(settings.read_text().replace('isolated-test', prefix))
    for name in ('alpha', 'beta'):
        env = root / f'projects/{name}/.env'
        env.write_text(env.read_text().replace('WORKSPACE_PHP_VERSION=8.4', 'WORKSPACE_PHP_VERSION=8.5'))
        shutil.rmtree(root / f'projects/{name}/.docker')
    fixture.sync()
    module = fixture.load_module()
    projects = [module.project(name) for name in ('alpha', 'beta')]
    original = module.compose

    def compose(p, *args, **kwargs):
        kwargs['capture'] = True
        return original(p, *args, **kwargs)

    module.compose = compose

    def dc(p, *args):
        return compose(p, *args, profiles=False).stdout.strip()

    def request(p, path='/'):
        return dc(p, 'exec', '-T', 'php', 'curl', '--fail', '--silent', f'http://nginx{path}')

    try:
        # Optional local bases make repeated checks fast/offline; defaults test full builds.
        for p in projects:
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
            (p['source'] / 'public/index.php').write_text('<?php echo "initial";')
            (p['source'] / 'public/asset.txt').write_text('asset-initial')
            (p['source'] / 'storage/app/public/probe.txt').write_text('upload-seed')
            print(f'Starting disposable {p["name"]}...', flush=True)
            module.up(p)
            assert request(p) == 'initial'
            assert request(p, '/asset.txt') == 'asset-initial'
            assert request(p, '/storage/probe.txt') == 'upload-seed'
        a, b = projects
        before = {p['name']: dc(p, 'ps', '-q') for p in projects}
        images = {p['name']: dc(p, 'images', '-q') for p in projects}
        (a['source'] / 'public/index.php').write_text('<?php echo "updated";')
        (a['source'] / 'public/asset.txt').write_text('asset-updated')
        for _ in range(15):
            if request(a) == 'updated':
                break
            time.sleep(1)
        assert request(a) == 'updated'
        assert request(a, '/asset.txt') == 'asset-updated'
        assert request(b) == 'initial'
        assert {p['name']: dc(p, 'ps', '-q') for p in projects} == before
        assert {p['name']: dc(p, 'images', '-q') for p in projects} == images
        dc(a, 'exec', '-T', '-u', 'www-data', 'php', 'sh', '-ec',
           'echo keep > storage/app/public/persist.txt; test -w storage/logs; test -w bootstrap/cache; '
           '! touch public/should-fail')
        # Changing the host seed must not replace initialized runtime volume contents.
        (a['source'] / 'storage/app/public/probe.txt').write_text('changed-host-seed')
        def no_build(p, *args, **kwargs):
            assert args[0] != 'build', 'Repeated startup unexpectedly rebuilt runtime images'
            return compose(p, *args, **kwargs)
        module.compose = no_build
        module.up(a)
        assert request(a, '/storage/probe.txt') == 'upload-seed'
        assert request(a, '/storage/persist.txt') == 'keep'
        assert dc(b, 'ps', '-q') == before['beta']
        module.check_isolation(a, json.loads(dc(a, 'config', '--format', 'json')))
        print('PASS: live PHP/assets, read-only source, writable seeded runtime, persistent volumes, image reuse, and project isolation.', flush=True)
    finally:
        module.compose = compose
        for p in projects:
            compose(p, 'down', '-v', '--remove-orphans', check=False, profiles=False)
            module.docker('network', 'rm', p['stack'] + '_edge', capture=True, check=False)
            for service in ('php', 'nginx'):
                module.docker('image', 'rm', f'{p["stack"]}-{service}:current', capture=True, check=False)
        fixture.doCleanups()


if __name__ == '__main__':
    main()
