#!/usr/bin/env python3
"""Opt-in PHP/FPM logging check; disposable images/containers, no published ports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, check=True):
    result = subprocess.run(['docker', *map(str, args)], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def main():
    prefix = 'php-log-check-' + uuid.uuid4().hex[:10]
    containers, networks, images = [], [], []
    with tempfile.TemporaryDirectory(prefix=prefix) as temp:
        root = Path(temp)
        root.chmod(0o755)
        source = root / 'app'
        source.mkdir(mode=0o755)
        shutil.copytree(ROOT / 'docker/templates', source / '.docker')
        (source / 'public').mkdir()
        (source / 'logs').mkdir()
        other = root / 'other-project-logs'
        other.mkdir()
        (other / 'php-error.log').write_text('other project untouched\n')
        # Unknown extensions must also be denied when the web root is the project root.
        (source / 'logs/private.txt').write_text('private log data')
        (source / 'logs/php-error.log').write_text('historical error\n')
        code = '<?php trigger_error("project-error-marker", E_USER_WARNING); echo "response-ok";'
        (source / 'index.php').write_text(code)
        (source / 'public/index.php').write_text(code)
        last_php = None
        try:
            for mode, public in [('development', '.'), ('production', 'public')]:
                for kind, filename in [('php', 'Dockerfile'), ('nginx', 'nginx.Dockerfile')]:
                    iid = source / f'{kind}-{mode}.iid'
                    tag = f'{prefix}-{kind}-{mode}'
                    args = ['build', '--progress=plain', '--tag', tag, '--target', mode,
                            '--build-arg', 'PHP_VERSION=8.5', '--build-arg', f'DOCUMENT_ROOT={public}',
                            '--iidfile', iid, '-f', source / '.docker' / filename, source]
                    print(f'Building {mode} {kind}...', flush=True)
                    docker(*args)
                    image = iid.read_text().strip()
                    images.append(tag)
                    if kind == 'php':
                        php_image = image
                    else:
                        nginx_image = image
                last_php = php_image
                bind = f'{source / "logs"}:/var/log/project'
                docker('run', '--rm', '--network', 'none', '-v', bind, '--entrypoint', 'sh',
                       php_image, '/usr/local/bin/prepare-logs')
                network = prefix + '-' + mode
                docker('network', 'create', network)
                networks.append(network)
                php_args = ['run', '-d', '--network', network, '--network-alias', 'php', '-v', bind]
                if mode == 'development':
                    php_args += ['-v', f'{source}:/var/www/html']
                else:
                    php_args += ['--read-only', '--tmpfs', '/tmp', '--tmpfs', '/usr/local/var/run']
                php = docker(*php_args, php_image).stdout.strip()
                containers.append(php)
                for _ in range(20):
                    if docker('exec', php, 'fpm-healthcheck', check=False).returncode == 0:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError('FPM did not become healthy')
                docker('exec', php, 'php', '-r', 'exit(file_exists($argv[1]) ? 1 : 0);',
                       other / 'php-error.log')
                if mode == 'production':
                    docker('exec', php, 'sh', '-c', 'test ! -e /var/www/html/logs')
                nginx_args = ['run', '-d', '--network', network,
                              '-e', f'DOCUMENT_ROOT={public}', '-e', 'FASTCGI_HTTPS=off',
                              '-e', 'NGINX_ENVSUBST_FILTER=^(DOCUMENT_ROOT|FASTCGI_HTTPS)$']
                if mode == 'development':
                    nginx_args += ['-v', f'{source}:/var/www/html:ro']
                nginx = docker(*nginx_args, nginx_image).stdout.strip()
                containers.append(nginx)
                for _ in range(20):
                    response = docker('exec', nginx, 'wget', '-qO-', 'http://127.0.0.1/', check=False)
                    if response.returncode == 0:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError('HTTP request failed: ' + response.stderr)
                assert 'response-ok' in response.stdout, response.stdout
                if mode == 'production':
                    assert 'project-error-marker' not in response.stdout, response.stdout
                logfile = source / 'logs/php-error.log'
                content = logfile.read_text()
                assert 'historical error' in content and 'project-error-marker' in content
                assert (other / 'php-error.log').read_text() == 'other project untouched\n'
                assert logfile.stat().st_mode & 0o004, 'Host cannot read log'
                # Verify both the real error file and a non-.log file are inaccessible.
                for url in ['/logs', '/logs/php-error.log', '/logs/private.txt']:
                    response = docker('exec', nginx, 'wget', '-S', '-O-',
                                      'http://127.0.0.1' + url, check=False)
                    assert '403 Forbidden' in response.stderr, response.stderr
                docker('rm', '-f', php)
                containers.remove(php)
                # Reinitialization must retain errors across container recreation.
                docker('run', '--rm', '--network', 'none', '-v', bind, '--entrypoint', 'sh',
                       php_image, '/usr/local/bin/prepare-logs')
                assert logfile.read_text() == content
                print(f'{mode}: FPM writes host-visible errors, /logs blocked, contents preserved', flush=True)
        finally:
            for container in containers:
                docker('rm', '-f', container, check=False)
            for network in networks:
                docker('network', 'rm', network, check=False)
            # Restore ownership of test logs so TemporaryDirectory can clean up on Linux.
            if last_php:
                docker('run', '--rm', '--network', 'none', '-v', f'{source / "logs"}:/var/log/project',
                       '--entrypoint', 'sh', last_php, '-c',
                       f'chown -R {os.getuid()}:{os.getgid()} /var/log/project', check=False)
            for image in set(images):
                docker('image', 'rm', image, check=False)


if __name__ == '__main__':
    main()
