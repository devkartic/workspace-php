#!/usr/bin/env python3
"""Opt-in two-stack isolation test. No host ports, real application data, or public ACME."""
import contextlib
import importlib.util
import io
import json
import subprocess
import uuid

from test_production import ProductionTests


def main():
    fixture = ProductionTests()
    fixture.setUp()
    prefix = 'isolation-smoke-' + uuid.uuid4().hex[:10]
    root = fixture.root
    path = root / 'docker/.env.production'
    path.write_text(path.read_text().replace('isolated-test', prefix))
    with (root / 'projects/alpha/.env').open('a') as stream:
        stream.write('WORKSPACE_DOMAIN_ALIASES=www.alpha.example.com\n')
    fixture.sync()
    spec = importlib.util.spec_from_file_location('smoke_stacks', root / 'scripts/stacks.py')
    stacks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stacks)
    stacks.MODE = 'production'
    original_compose = stacks.compose

    def compose(p, *args, **kwargs):
        kwargs['capture'] = True
        return original_compose(p, *args, **kwargs)

    stacks.compose = compose
    projects = [stacks.project(n) for n in ('alpha', 'beta')]
    a, b = projects

    def dc(p, *args):
        return compose(p, *args, profiles=False).stdout

    def gateway_http(projects):
        stacks.gateway_generate(projects)  # Validate actual public configuration first.
        _, _, dest = stacks.gateway_paths()
        file = dest / 'caddy/Caddyfile'
        content = file.read_text()
        hosts = {host for p in projects for host in [p['host'], *p['aliases']]}
        content = '\n'.join('http://' + line if line.removesuffix(' {') in hosts else line
                            for line in content.splitlines()) + '\n'
        content = content.replace('redir https://', 'redir http://')
        file.write_text(content)
        config = json.loads((dest / 'compose.json').read_text())
        config['services']['caddy']['ports'] = []
        (dest / 'compose.json').write_text(json.dumps(config))

    def request(p):
        return stacks.gateway_compose('exec', '-T', 'caddy', 'wget', '-qO-', '--header',
                                     f'Host: {p["host"]}', 'http://127.0.0.1', capture=True).stdout

    def cannot_connect(p, address, port):
        code = '$s=@fsockopen($argv[1],(int)$argv[2],$e,$m,1); exit($s?1:0);'
        dc(p, 'exec', '-T', 'php', 'php', '-r', code, address, str(port))

    try:
        for p in projects:
            source = p['source']
            (source / 'public/index.php').write_text(f'<?php echo "{p["name"]}:" . (ini_get("display_errors") ? "on" : "off");')
            (source / 'public/asset.txt').write_text('static ok')
            (source / 'public/.env').write_text('private')
            (source / 'storage/app/public/probe.txt').write_text('upload ok')
        for p in projects:
            print(f'Building and starting {p["name"]} with its own database/cache...', flush=True)
            stacks.up(p)
            config = json.loads(dc(p, 'config', '--format', 'json'))
            assert not any(s.get('ports') for s in config['services'].values())
            # Verify the image itself, without the runtime .env mount.
            stacks.docker('run', '--rm', '--entrypoint', 'sh', f'{p["stack"]}-php:current', '-ec',
                          'test ! -f /var/www/html/.env && test ! -f /var/www/html/.docker/infra.production.env', capture=True)
        print('Checking private networks and live gateway routing...', flush=True)
        for p, other in ((a, b), (b, a)):
            for service, port in (('mariadb', 3306), ('redis', 6379), ('php', 9000)):
                container = dc(other, 'ps', '-q', service).strip()
                info = json.loads(stacks.docker('inspect', container, capture=True).stdout)[0]
                address = next(iter(info['NetworkSettings']['Networks'].values()))['IPAddress']
                cannot_connect(p, address, port)
        gateway_http([a])
        stacks.gateway_compose('up', '-d', '--wait', capture=True)
        stacks.gateway_connect([a])
        gateway_id = stacks.gateway_compose('ps', '-q', 'caddy', capture=True).stdout.strip()
        assert request(a) == 'alpha:off'
        gateway_http(projects)
        stacks.gateway_connect(projects)
        assert stacks.gateway_compose('ps', '-q', 'caddy', capture=True).stdout.strip() == gateway_id
        assert request(b) == 'beta:off'
        redirect = stacks.gateway_compose(
            'exec', '-T', 'caddy', 'sh', '-ec',
            'printf "POST /verify?x=1 HTTP/1.1\\r\\nHost: www.alpha.example.com\\r\\nContent-Length: 0\\r\\nConnection: close\\r\\n\\r\\n" | nc -w 3 127.0.0.1 80',
            capture=True).stdout.lower()
        assert '308 permanent redirect' in redirect, redirect
        assert 'location: http://alpha.example.com/verify?x=1' in redirect, redirect
        dest = stacks.gateway_paths()[2]
        before = [(dest / name).read_bytes() for name in ('compose.json', 'caddy/Caddyfile')]
        aliases = list(a['aliases'])
        a['aliases'].append(b['host'])
        try:
            stacks.gateway_generate(projects)
        except ValueError:
            pass
        else:
            raise AssertionError('Duplicate alias was accepted')
        a['aliases'] = aliases
        assert [(dest / name).read_bytes() for name in ('compose.json', 'caddy/Caddyfile')] == before
        assert request(b) == 'beta:off'
        assert dc(a, 'exec', '-T', 'nginx', 'wget', '-qO-', 'http://127.0.0.1/storage/probe.txt') == 'upload ok'
        private = compose(a, 'exec', '-T', 'nginx', 'wget', '-qO-', 'http://127.0.0.1/.env', check=False)
        assert private.returncode != 0
        for p in projects:
            assert 'NOAUTH' in dc(p, 'exec', '-T', 'redis', 'redis-cli', 'ping')
            assert 'PONG' in dc(p, 'exec', '-T', 'redis', 'sh', '-ec', 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping')
        print('Checking project-specific backup/restore and independent rebuilds...', flush=True)
        dc(a, 'exec', '-T', 'mariadb', 'sh', '-ec', 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -uroot app -e "CREATE TABLE smoke(id INT); INSERT INTO smoke VALUES(42)"')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stacks.database('backup', 'alpha')
        dc(a, 'exec', '-T', 'mariadb', 'sh', '-ec', 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -uroot app -e "DROP TABLE smoke"')
        stacks.database('restore', 'alpha', output.getvalue().strip())
        assert '42' in dc(a, 'exec', '-T', 'mariadb', 'sh', '-ec', 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -uroot app -e "SELECT * FROM smoke"')
        beta_ids = sorted(dc(b, 'ps', '-q').split())
        beta_image = json.loads(stacks.docker('image', 'inspect', f'{b["stack"]}-php:current', capture=True).stdout)[0]['Id']
        dockerfile = a['source'] / '.docker/Dockerfile'
        dockerfile.write_text(dockerfile.read_text() + '\nRUN touch /alpha-only\n')
        (a['source'] / '.docker/compose.override.yml').write_text('''services:
  worker:
    image: ${STACK_NAME}-php:${IMAGE_TAG}
    command: [php, -r, "while (true) { sleep(1); }"]
    restart: unless-stopped
    mem_limit: 64m
''')
        a = stacks.sync(['alpha'])[0]
        stacks.up(a)
        assert dc(a, 'ps', '-q', 'worker').strip()
        assert sorted(dc(b, 'ps', '-q').split()) == beta_ids
        assert json.loads(stacks.docker('image', 'inspect', f'{b["stack"]}-php:current', capture=True).stdout)[0]['Id'] == beta_image
        assert request(b) == 'beta:off'
        compose(a, 'down', profiles=False)
        assert sorted(dc(b, 'ps', '-q').split()) == beta_ids
        assert request(b) == 'beta:off'
        assert stacks.gateway_compose('ps', '-q', 'caddy', capture=True).stdout.strip() == gateway_id
        print('PASS: separate images, backend connectivity blocked both ways, gateway hot attachment/reload, alias 308/URI preservation, collision rollback, image secrets excluded, upload storage, Redis auth, backup/restore, independent worker rebuild and project shutdown.', flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout or '')
        print(error.stderr or '')
        raise
    finally:
        stacks.gateway_compose('down', '-v', capture=True, check=False)
        for p in projects:
            compose(p, 'down', '-v', '--remove-orphans', check=False, profiles=False)
            stacks.docker('network', 'rm', p['stack'] + '_edge', capture=True, check=False)
            for service in ('php', 'nginx'):
                stacks.docker('image', 'rm', f'{p["stack"]}-{service}:current', capture=True, check=False)
        fixture.doCleanups()


if __name__ == '__main__':
    main()
