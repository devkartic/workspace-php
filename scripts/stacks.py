#!/usr/bin/env python3
"""Independent project stacks; every Docker operation has an explicit owner."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from string import Template

ROOT = Path(__file__).resolve().parents[1]
MODE = os.environ.get('WORKSPACE_ENV', 'development')
OWNER = 'io.php-workspace.owner'
# Latest stable branch tested by this workspace; registration never needs a network lookup.
DEFAULT_PHP_VERSION = '8.5'
PHP_VERSIONS = {'development': {'7.4', '8.1', '8.3', '8.4', '8.5'}, 'production': {'8.3', '8.4', '8.5'}}
RESOURCE_DEFAULTS = {
    'PHP_CPUS': '1', 'PHP_MEMORY': '1g', 'NGINX_CPUS': '0.5',
    'NGINX_MEMORY': '256m', 'DATABASE_CPUS': '1', 'DATABASE_MEMORY': '1g',
    'REDIS_CPUS': '0.5', 'REDIS_MEMORY': '256m', 'REDIS_MAXMEMORY': '128mb',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_dotenv(content, label):
    """Read literal dotenv values without evaluating shell or variable expressions."""
    values = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        require(key not in values, f'Duplicate {key} in {label}')
        values[key] = value
    return values


def dotenv(path):
    require(path.is_file(), f'Missing {path}')
    return parse_dotenv(path.read_text(), str(path))


def valid_name(value):
    return bool(re.fullmatch(r'[a-z][a-z0-9_]{0,31}', value))


def valid_host(value, public=False):
    """Accept literal DNS hostnames, never URLs, wildcards, or IP addresses."""
    labels = value.split('.')
    valid = (len(value) <= 253 and len(labels) >= 2 and not labels[-1].isdigit()
             and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels))
    if public:
        valid = (valid and re.fullmatch(r'[a-z]{2,}', labels[-1]) is not None
                 and labels[-1] not in {'local', 'localhost', 'test', 'internal', 'invalid'}
                 and not value.endswith('.home.arpa'))
    return bool(valid)


def domains(app):
    host = app.get('WORKSPACE_HOSTNAME', '')
    aliases = app.get('WORKSPACE_DOMAIN_ALIASES', '').split()
    values = [host, *aliases]
    require(all(valid_host(value, MODE == 'production') for value in values),
            'Use valid ' + ('public ' if MODE == 'production' else '') + 'hostnames without a scheme or port')
    require(len(values) == len(set(values)), 'Duplicate domain alias or alias matching the canonical hostname')
    return host, aliases


def check_domains(projects):
    """A gateway hostname must have exactly one project owner."""
    owners = {}
    for p in projects:
        for host in [p['host'], *p['aliases']]:
            require(host not in owners, f'Duplicate project domain: {host}')
            owners[host] = p['name']


def relative(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', value))


def secret(value, label):
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]{32,}', value))
            and not value.upper().startswith(('REPLACE_', 'CHANGE_', 'EXAMPLE_')),
            f'{label}: generate a unique secret with openssl rand -hex 32')


def infra():
    require(MODE in {'development', 'production'}, 'ENV must be development or production')
    path = ROOT / 'docker' / ('.env.production' if MODE == 'production' else '.env')
    if not path.exists() and MODE == 'development':
        path = ROOT / 'docker/.env.example'
    data = dotenv(path)
    namespace = data.get('COMPOSE_PROJECT_NAME', 'php-workspace')
    require(re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', namespace), 'Invalid COMPOSE_PROJECT_NAME namespace')
    return data, namespace


def project_names():
    settings, _ = infra()
    if MODE == 'production':
        result = settings.get('PRODUCTION_PROJECTS', '').split()
        require(result, 'Explicitly set PRODUCTION_PROJECTS in docker/.env.production')
    else:
        result = [p.parent.name for p in sorted((ROOT / 'projects').glob('*/.env'))
                  if dotenv(p).get('WORKSPACE_PROJECT_NAME')]
    require('gateway' not in result, "Project name 'gateway' is reserved for the shared gateway")
    require(len(result) == len(set(result)) and all(valid_name(n) for n in result), 'Invalid/duplicate project list')
    return result


@contextmanager
def lock(key=None):
    path = ROOT / 'docker' / (f'.stack-{MODE}-{key}.lock' if key else '.workspace-lock')
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError(f'Workspace is busy: {path}. Remove this lock only after confirming its command is no longer running')
    try:
        yield
    finally:
        path.rmdir()


def write_new(path, content, created):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            stream.write(content)
        created.append(path)


def scaffold(source, created):
    target = source / '.docker'
    for path in (ROOT / 'docker/templates').iterdir():
        if path.is_file():
            content = path.read_text()
            if path.name in {'Dockerfile.dockerignore', 'nginx.Dockerfile.dockerignore'}:
                existing = source / '.dockerignore'
                if existing.is_file():
                    content = existing.read_text() + '\n' + content
            write_new(target / path.name, content, created)
    write_new(target / 'compose.yml', (ROOT / 'docker/docker-compose.yml').read_text(), created)
    defaults = dict(RESOURCE_DEFAULTS)
    if MODE == 'production':
        defaults['NGINX_MEMORY'] = '128m'
    content = ('# Private, project-only infrastructure. Never commit this file.\n'
               f'MARIADB_ROOT_PASSWORD={secrets.token_hex(32)}\n'
               + ''.join(f'{key}={value}\n' for key, value in defaults.items()))
    write_new(target / f'infra.{MODE}.env', content, created)


def project(n, created=None):
    require(n != 'gateway', "Project name 'gateway' is reserved for the shared gateway")
    require(valid_name(n), 'Specify a valid NAME (lowercase letters, numbers, underscores)')
    if MODE == 'production':
        require(n in project_names(), f'{n} is not selected in PRODUCTION_PROJECTS')
    _, prefix = infra()
    source = (ROOT / 'projects' / n).resolve()
    app = dotenv(source / '.env')
    require(app.get('WORKSPACE_PROJECT_NAME') == n, f'{n}: registration name must match projects/{n}')
    version = app.get('WORKSPACE_PHP_VERSION', '')
    require(version in PHP_VERSIONS[MODE], f'{n}: unsupported PHP version')
    public = app.get('WORKSPACE_DOCUMENT_ROOT', '.')
    require(public == '.' or relative(public), f'{n}: invalid document root')
    require((source / public).is_dir() and (source / public).resolve().is_relative_to(source), f'{n}: public directory must exist inside source')
    host, aliases = domains(app)
    db = app.get('DB_DATABASE', n)
    user = app.get('DB_USERNAME') or f'{n[:27]}_user'
    password = app.get('DB_PASSWORD') or f'development_{n}_password'
    redis_password = app.get('REDIS_PASSWORD', '')
    if MODE == 'development' and redis_password in {'', 'null'}:
        redis_password = f'development_{n}_redis'
    require(valid_name(db) and valid_name(user) and user != 'root', f'{n}: use a non-root DB_USERNAME; follow docs/PRODUCTION.md migration instructions')
    require(app.get('DB_HOST', 'mariadb') == 'mariadb' and app.get('REDIS_HOST', 'redis') == 'redis', f'{n}: use project-local hosts mariadb and redis')
    require(app.get('DB_PORT', '3306') == '3306' and app.get('REDIS_PORT', '6379') == '6379', f'{n}: use internal database/cache ports')
    require(re.fullmatch(r'[A-Za-z0-9_-]+', password) and re.fullmatch(r'[A-Za-z0-9_-]+', redis_password), f'{n}: passwords must use A-Z, a-z, 0-9, _ or -')
    writable = app.get('WORKSPACE_WRITABLE_PATHS', '').split()
    for value in writable:
        require(relative(value), f'{n}: invalid writable directory')
        path = source / value
        require(path.is_dir() and path.resolve().is_relative_to(source), f'{n}: writable directory must exist inside source')
        require(not any(source.joinpath(*Path(value).parts[:i]).is_symlink() for i in range(1, len(Path(value).parts) + 1)), f'{n}: writable directory cannot be a symlink')
        require(public != '.' and not (value == public or value.startswith(public + '/') or public.startswith(value + '/')), f'{n}: writable directory overlaps public root')
    require(len(writable) == len(set(writable)), f'{n}: duplicate writable directories')
    for a in writable:
        require(not any(b != a and b.startswith(a + '/') for b in writable), f'{n}: overlapping writable directories')
    storage, cache = app.get('WORKSPACE_STORAGE_VOLUME', ''), app.get('WORKSPACE_CACHE_VOLUME', '')
    require(bool(storage) == bool(cache), f'{n}: provide both existing runtime volumes')
    if storage:
        require(all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', x) for x in (storage, cache)) and storage != cache, f'{n}: invalid external volume names')
        require(not any(a == 'storage' or a.startswith('storage/') or a == 'bootstrap/cache' or a.startswith('bootstrap/cache/') or 'bootstrap/cache'.startswith(a + '/') for a in writable), f'{n}: writable paths overlap external volumes')
    if MODE == 'production':
        require(public != '.' and not any(source.joinpath(*Path(public).parts[:i]).is_symlink() for i in range(1, len(Path(public).parts) + 1)), f'{n}: require a dedicated public directory without symlink parents')
        require(app.get('APP_ENV') == 'production' and app.get('APP_DEBUG', '').lower() == 'false' and app.get('APP_URL') == f'https://{host}', f'{n}: set APP_ENV=production, APP_DEBUG=false and HTTPS APP_URL')
        require(app.get('DB_DATABASE') and app.get('DB_USERNAME') and app.get('DB_PASSWORD') and app.get('REDIS_PASSWORD'), f'{n}: explicit application credentials required')
        secret(password, f'{n} DB_PASSWORD')
        secret(redis_password, f'{n} REDIS_PASSWORD')
    if created is not None:
        scaffold(source, created)
    private = dotenv(source / f'.docker/infra.{MODE}.env')
    secret(private.get('MARIADB_ROOT_PASSWORD', ''), f'{n} MARIADB_ROOT_PASSWORD')
    allowed_private = {'MARIADB_ROOT_PASSWORD', *RESOURCE_DEFAULTS}
    require(not set(private) - allowed_private, f'{n}: unsupported private infrastructure setting')
    stack = f'{prefix}-{MODE}-{n}'
    runtime = ROOT / 'docker/stacks' / MODE / n
    tag = app.get('WORKSPACE_IMAGE_TAG', 'current')
    require(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag), f'{n}: invalid image tag')
    env = {**app, **private, 'DB_DATABASE': db, 'DB_USERNAME': user, 'DB_PASSWORD': password,
           'REDIS_PASSWORD': redis_password, 'STACK_NAME': stack, 'PROJECT_NAME': n,
           'PROJECT_SOURCE': str(source), 'RUNTIME_PATH': str(runtime), 'IMAGE_TAG': tag,
           'BUILD_TARGET': MODE, 'FASTCGI_HTTPS': 'on' if MODE == 'production' else 'off',
           'WORKSPACE_DOCUMENT_ROOT': public}
    # Do not inherit Compose controls, profiles, or shell values over the project's settings.
    environment = {k: v for k, v in os.environ.items() if not k.startswith('COMPOSE_')}
    protected = {'PATH', 'HOME', 'SHELL', 'PYTHONPATH', 'TMPDIR', 'WORKSPACE_ENV'}
    environment.update({key: value for key, value in env.items()
                        if key not in protected and not key.startswith(('DOCKER_', 'COMPOSE_'))})
    return {'name': n, 'source': source, 'app': app, 'env': environment, 'stack': stack,
            'runtime': runtime, 'host': host, 'aliases': aliases, 'public': public, 'writable': writable}


def compose_args(p, overlay=None, profiles=False):
    source = p['source']
    files = [source / '.docker/compose.yml', overlay or p['runtime'] / 'compose.json', source / '.docker/compose.override.yml']
    optional = source / f'.docker/compose.override.{MODE}.yml'
    if optional.is_file():
        files.append(optional)
    cmd = ['docker', 'compose', '--project-directory', str(source), '--env-file', str(source / f'.docker/infra.{MODE}.env'), '-p', p['stack']]
    for path in files:
        cmd += ['-f', str(path)]
    if profiles:
        cmd += ['--profile', 'init']
    return cmd


def compose(p, *args, overlay=None, capture=False, check=True, profiles=True):
    return subprocess.run(compose_args(p, overlay, profiles) + list(args), env=p['env'],
                          text=True, capture_output=capture, check=check)


def mount(source, target, readonly=False, kind='bind'):
    result = {'type': kind, 'source': str(source), 'target': target, 'read_only': readonly}
    if kind == 'bind':
        result['bind'] = {'create_host_path': False}
    return result


def overlay_config(p):
    source, app = p['source'], p['app']
    services = {'php': {'volumes': []}, 'nginx': {'volumes': []}}
    volumes = {}
    if MODE == 'development':
        services['php']['volumes'].append(mount(source, '/var/www/html'))
        services['nginx']['volumes'].append(mount(source, '/var/www/html', True))
        port = app.get('WORKSPACE_HTTP_PORT', '')
        if port:
            require(port.isdigit() and 1024 <= int(port) <= 65535, f"{p['name']}: invalid HTTP port")
            services['nginx']['ports'] = [f'127.0.0.1:{port}:80']
    else:
        services['php'].update(read_only=True, tmpfs=['/tmp', '/usr/local/var/run'])
        services['php']['volumes'].append(mount(source / '.env', '/var/www/html/.env', True))
    if (source / 'dev/php.ini').is_file():
        services['php']['volumes'].append(mount(source / 'dev/php.ini', '/usr/local/etc/php/conf.d/zz-project.ini', True))
    paths = {value: None for value in p['writable']}
    if app.get('WORKSPACE_STORAGE_VOLUME'):
        paths.update({'storage': app['WORKSPACE_STORAGE_VOLUME'], 'bootstrap/cache': app['WORKSPACE_CACHE_VOLUME']})
    for value, external in paths.items():
        v = 'runtime_' + value.replace('/', '_')
        require(v not in volumes, f"{p['name']}: colliding runtime volume names")
        if external:
            volumes[v] = {'external': True, 'name': external}
            services['php']['volumes'].append(mount(v, f'/var/www/html/{value}', kind='volume'))
        elif MODE == 'production':
            volumes[v] = {}
            services['php']['volumes'].append(mount(v, f'/var/www/html/{value}', kind='volume'))
        # Nginx needs only public storage; use nocopy so PHP initializes ownership/content.
        if (external or MODE == 'production') and (value == 'storage' or value.startswith('storage/')):
            m = mount(v, f'/var/www/html/{value}', True, 'volume')
            m['volume'] = {'nocopy': True}
            services['nginx']['volumes'].append(m)
    return {'services': services, 'volumes': volumes}


def check_isolation(p, resolved):
    require(resolved['name'] == p['stack'], 'Compose identity changed')
    for key, network in resolved.get('networks', {}).items():
        expected = p['stack'] + ('_backend' if key == 'default' else '_' + key)
        require(network.get('name') == expected, f'{key}: network must belong to this project')
        require(not network.get('external') or key == 'edge', 'Only the project edge network may be external')
    for key, volume in resolved.get('volumes', {}).items():
        if volume.get('external'):
            require(volume['name'] in {p['app'].get('WORKSPACE_STORAGE_VOLUME'), p['app'].get('WORKSPACE_CACHE_VOLUME')}, 'Unapproved external volume')
        else:
            require(volume.get('name') == f"{p['stack']}_{key}", 'Volume must belong to this project')
    for kind in ('secrets', 'configs'):
        for item in resolved.get(kind, {}).values():
            require(not item.get('external'), 'External secrets/configs are not project-isolated')
            if item.get('file'):
                require(Path(item['file']).resolve().is_relative_to(p['source']), 'Secret/config files must belong to this project')
    for n, service in resolved['services'].items():
        require(not any(service.get(k) for k in ('privileged', 'network_mode', 'pid', 'ipc', 'devices', 'cap_add', 'container_name', 'external_links', 'links', 'volumes_from', 'use_api_socket', 'uts')),
                f'{n}: host/shared namespaces, devices and privileged settings are not supported')
        nets = set(service.get('networks', {}))
        require(('edge' not in nets or n == 'nginx') and nets <= set(resolved['networks']), f'{n}: only nginx may join the project edge')
        if MODE == 'production':
            require(not service.get('ports'), f'{n}: production project services must not publish host ports')
        else:
            require(all(x.get('host_ip') == '127.0.0.1' for x in service.get('ports', [])), f'{n}: development ports must bind to localhost')
        for v in service.get('volumes', []):
            if v['type'] == 'bind':
                host = Path(v['source']).resolve()
                require(host.is_relative_to(p['source']) or host.is_relative_to(p['runtime']), f'{n}: bind mounts must stay within this project')
                require(not str(host).endswith(('.sock', '/docker.sock')), f'{n}: host sockets cannot be mounted')
        if n in {'php', 'nginx'} or service.get('build'):
            require(service.get('image', '').startswith(p['stack'] + '-'), f'{n}: use a project-specific image tag')
            if service.get('build'):
                require(Path(service['build']['context']).resolve() == p['source'], f'{n}: build context must be this project')
                dockerfile = Path(service['build'].get('dockerfile', 'Dockerfile'))
                require((p['source'] / dockerfile).resolve().is_relative_to(p['source']), f'{n}: Dockerfile must belong to this project')
                require(service['build'].get('network', 'default') in {'default', 'none'} and not service['build'].get('additional_contexts'),
                        f'{n}: host/shared build networks and extra contexts are not supported')
    if MODE == 'production':
        require(resolved['services']['php'].get('read_only'), 'Production PHP must have a read-only filesystem')
        for n, service in resolved['services'].items():
            for v in service.get('volumes', []):
                if v['type'] == 'bind':
                    require(v.get('read_only'), f'{n}: production host bind mounts must be read-only')
                    require(v['target'] != '/var/www/html', 'Production source must be baked into its image')


def sync(names):
    with lock():
        return generate(names)


def generate(names):
    created, staged = [], []
    try:
        projects = [project(n, created) for n in names]
        check_domains(projects)
        ports = [p['app'].get('WORKSPACE_HTTP_PORT') for p in projects if p['app'].get('WORKSPACE_HTTP_PORT') and MODE == 'development']
        require(len(ports) == len(set(ports)), 'Duplicate project HTTP port')
        external = [p['app'].get(k) for p in projects for k in ('WORKSPACE_STORAGE_VOLUME', 'WORKSPACE_CACHE_VOLUME') if p['app'].get(k)]
        require(len(external) == len(set(external)), 'Projects cannot share adopted runtime volumes')
        registry = ROOT / 'docker/stacks/volume-owners.json'
        owners = json.loads(registry.read_text()) if registry.exists() else {}
        for p in projects:
            for key in ('WORKSPACE_STORAGE_VOLUME', 'WORKSPACE_CACHE_VOLUME'):
                volume = p['app'].get(key)
                if volume:
                    require(volume not in owners or owners[volume] == p['stack'],
                            f"{volume}: adopted volume is reserved for another stack; copy data to a separate volume")
                    owners[volume] = p['stack']
        for p in projects:
            p['runtime'].mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix='.sync.', dir=p['runtime']))
            staged.append((p, temp))
            candidate = temp / 'compose.json'
            candidate.write_text(json.dumps(overlay_config(p), indent=2) + '\n')
            result = compose(p, 'config', '--format', 'json', overlay=candidate, capture=True, check=False)
            require(result.returncode == 0, f"{p['name']}: Compose validation failed: {result.stderr}")
            check_isolation(p, json.loads(result.stdout))
            env = p['env']
            db, user, password = env['DB_DATABASE'], env['DB_USERNAME'], env['DB_PASSWORD']
            (temp / 'databases.sql').write_text("SET sql_mode = 'NO_BACKSLASH_ESCAPES';\n"
                f"CREATE DATABASE IF NOT EXISTS `{db}`;\nCREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}';\n"
                f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'%';\n")
            (temp / 'verify-databases.sh').write_text(f"#!/bin/sh\nset -eu\nMYSQL_PWD={shlex.quote(password)} mariadb -h mariadb -u{user} {db} -e 'SELECT 1' >/dev/null\n")
        for p, temp in staged:
            for path in temp.iterdir():
                os.replace(path, p['runtime'] / path.name)
        registry.parent.mkdir(parents=True, exist_ok=True)
        temporary = registry.with_suffix('.pending')
        temporary.write_text(json.dumps(owners, indent=2) + '\n')
        os.replace(temporary, registry)
        return projects
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        for path in {x.parent for x in created}:
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        raise
    finally:
        for _, path in staged:
            shutil.rmtree(path, ignore_errors=True)


def docker(*args, capture=False, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=capture, check=check)


def edge(p):
    n = p['stack'] + '_edge'
    result = docker('network', 'inspect', n, capture=True, check=False)
    if result.returncode:
        docker('network', 'create', '--label', f'{OWNER}={p["stack"]}', n, capture=True)
    else:
        require(json.loads(result.stdout)[0].get('Labels', {}).get(OWNER) == p['stack'], f'Network {n} has a different owner')


def gateway_paths():
    settings, prefix = infra()
    dest = ROOT / 'docker/stacks' / MODE / 'gateway'
    return settings, prefix + '-' + MODE + '-gateway', dest


def gateway_compose(*args, capture=False, check=True, config_path=None):
    _, stack, dest = gateway_paths()
    environment = {k: v for k, v in os.environ.items() if not k.startswith('COMPOSE_')}
    return subprocess.run(['docker', 'compose', '-p', stack, '-f', str(config_path or dest / 'compose.json'), *args],
                          text=True, capture_output=capture, check=check, env=environment)


def gateway_caddyfile(projects, settings):
    """Render canonical routes and method-preserving alias redirects."""
    check_domains(projects)
    content = []
    scheme = 'https' if MODE == 'production' else 'http'
    port = settings.get('NGINX_HTTP_PORT', '80')
    require(port.isdigit() and 1 <= int(port) <= 65535, 'NGINX_HTTP_PORT must be a port number')
    suffix = ':' + port if MODE == 'development' and port != '80' else ''
    if MODE == 'production':
        email = settings.get('ACME_EMAIL', '')
        require(re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email), 'Set a valid ACME_EMAIL')
        content += ['{', f'\temail {email}', '}', '']
    for p in projects:
        address = p['host'] if MODE == 'production' else 'http://' + p['host']
        content += [f'{address} {{', f'\treverse_proxy {p["name"]}-web:80', '\tlog', '}', '']
        for alias in p['aliases']:
            address = alias if MODE == 'production' else 'http://' + alias
            target = f'{scheme}://{p["host"]}{suffix}'
            content += [f'{address} {{', f'\tredir {target}{{uri}} 308', '}', '']
    content += [':80 {', '\trespond "Unknown host" 404', '}']
    return '\n'.join(content) + '\n'


def gateway_generate(projects):
    settings, stack, dest = gateway_paths()
    content = gateway_caddyfile(projects, settings)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'caddy').mkdir(exist_ok=True)
    candidate = dest / 'caddy/Caddyfile.pending'
    candidate.write_text(content)
    config = {'name': stack, 'services': {'caddy': {'image': 'caddy:2-alpine', 'restart': 'unless-stopped',
        'ports': [f'{settings.get("NGINX_HTTP_PORT", "80")}:80'] + (['443:443'] if MODE == 'production' else []),
        'volumes': [f'{dest}/caddy:/etc/caddy:ro', 'certificates:/data', 'config:/config'],
        'networks': ['control'], 'cpus': settings.get('EDGE_CPUS', '0.5'), 'mem_limit': settings.get('EDGE_MEMORY', '256m'),
        'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
        'healthcheck': {'test': ['CMD', 'wget', '-qO-', 'http://127.0.0.1:2019/config/'], 'interval': '10s', 'timeout': '5s', 'retries': 5}}},
        'networks': {'control': {}}, 'volumes': {'certificates': {}, 'config': {}}}
    pending = dest / 'compose.pending.json'
    pending.write_text(json.dumps(config, indent=2) + '\n')
    try:
        gateway_compose('run', '--rm', '--no-deps', 'caddy', 'caddy', 'validate', '--adapter', 'caddyfile',
                        '--config', '/etc/caddy/Caddyfile.pending', capture=True, config_path=pending)
        os.replace(candidate, dest / 'caddy/Caddyfile')
        os.replace(pending, dest / 'compose.json')
    finally:
        candidate.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)


def gateway_connect(projects):
    result = gateway_compose('ps', '-q', 'caddy', capture=True)
    container = result.stdout.strip()
    require(container, 'Gateway is not running; use make gateway-up')
    connected = json.loads(docker('inspect', container, capture=True).stdout)[0]['NetworkSettings']['Networks']
    wanted = {p['stack'] + '_edge' for p in projects}
    for p in projects:
        edge(p)
        if p['stack'] + '_edge' not in connected:
            docker('network', 'connect', p['stack'] + '_edge', container)
    # Disconnect removed routes only within this gateway's namespace.
    _, prefix = infra()
    for network in connected:
        if network.startswith(f'{prefix}-{MODE}-') and network.endswith('_edge') and network not in wanted:
            docker('network', 'disconnect', network, container)
    gateway_compose('exec', '-T', 'caddy', 'caddy', 'reload', '--adapter', 'caddyfile', '--config', '/etc/caddy/Caddyfile')


def database(action, n, filename=''):
    p = project(n)
    db = p['env']['DB_DATABASE']
    require((p['runtime'] / 'compose.json').is_file(), 'Run make validate NAME=... first')
    if action == 'backup':
        directory = ROOT / 'docker/backups' / MODE / n
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = directory / f'{stamp}.sql'
        fd, temporary = tempfile.mkstemp(prefix='.backup-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                command = ['exec', '-T', 'mariadb', 'sh', '-ec', 'export MYSQL_PWD="$MARIADB_ROOT_PASSWORD"; exec mariadb-dump -uroot --single-transaction --quick --hex-blob --routines --events --triggers "$1"', 'sh', db]
                # Keep streaming dumps out of memory, using the same resolved project command.
                result = compose_command(p, command, stdout=stream)
                require(result.returncode == 0, 'Backup failed; partial dump discarded')
                stream.flush()
                os.fsync(stream.fileno())
            require(Path(temporary).stat().st_size, 'Empty backup discarded')
            os.replace(temporary, target)
            print(target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        source = Path(filename).resolve() if filename else None
        require(source and source.is_file() and source.stat().st_size, 'Restore requires an explicit, nonempty trusted SQL FILE')
        with source.open('rb') as stream:
            result = compose_command(p, ['exec', '-T', 'mariadb', 'sh', '-ec', 'export MYSQL_PWD="$MARIADB_ROOT_PASSWORD"; exec mariadb -uroot "$1"', 'sh', db], stdin=stream)
        require(result.returncode == 0, 'Restore failed (it may be partially applied)')


def compose_command(p, args, **kwargs):
    return subprocess.run(compose_args(p) + args, env=p['env'], **kwargs)


def up(p):
    require(all(p['app'].get(key) == p['env'][key] for key in ('DB_USERNAME', 'DB_PASSWORD', 'REDIS_PASSWORD')),
            f"{p['name']}: configure explicit project database/Redis credentials in the application .env before startup")
    edge(p)
    compose(p, 'build')
    compose(p, 'run', '--rm', '--no-deps', 'nginx', 'nginx', '-t')
    compose(p, 'up', '-d', '--wait', 'mariadb', 'redis')
    compose(p, 'run', '--rm', 'database-init')
    compose(p, 'up', '-d', '--wait', '--remove-orphans', profiles=False)


def prepare_examples():
    """Bootstrap registered development examples without replacing private settings."""
    require(MODE == 'development', 'Example bootstrapping is development-only')
    for example in sorted((ROOT / 'projects').glob('*/.env.example')):
        target = example.with_name('.env')
        if not target.exists() and dotenv(example).get('WORKSPACE_PROJECT_NAME'):
            shutil.copyfile(example, target)
            target.chmod(0o644)  # Development examples contain no production credentials.


def doctor():
    """Check prerequisites without creating files or starting services."""
    require(sys.version_info >= (3, 9), 'Python 3.9 or newer is required')
    for tool in ('docker', 'make'):
        require(shutil.which(tool), f'Missing required tool: {tool}')
    result = docker('compose', 'version', capture=True, check=False)
    require(result.returncode == 0, 'Docker Compose v2 is required')
    result = docker('info', capture=True, check=False)
    require(result.returncode == 0, 'Cannot reach Docker; start Docker Engine/Desktop and check socket permissions')
    print('Required tools and Docker daemon are available.')


def setup():
    doctor()
    target = ROOT / 'docker' / ('.env.production' if MODE == 'production' else '.env')
    with lock():
        if not target.exists():
            shutil.copyfile(target.with_name(target.name + '.example'), target)
        target.chmod(0o600)
        if MODE == 'development':
            prepare_examples()
    if MODE == 'production':
        print('Configure docker/.env.production and selected application settings, then run make validate ENV=production NAME=api.')
    else:
        sync_projects(project_names())
        print('Setup complete. Run make project-up NAME=api, then make gateway-up for hostname routing.')


def registration_settings(n, version, host, public, port):
    return (f'\n# Docker registration. Use make project-up NAME={n} after changes.\n'
            f'WORKSPACE_PROJECT_NAME={n}\nWORKSPACE_PHP_VERSION={version}\n'
            '# Canonical hostname; keep APP_URL in sync. See docs/PRODUCTION.md for DNS.\n'
            f'WORKSPACE_HOSTNAME={host}\n'
            '# Optional space-separated hostnames that redirect to the canonical domain.\n'
            'WORKSPACE_DOMAIN_ALIASES=\n'
            f'WORKSPACE_DOCUMENT_ROOT={public}\n'
            '# Optional development-only localhost port.\n'
            f'WORKSPACE_HTTP_PORT={port}\n')


def register(args):
    """Register transactionally; existing application values and code remain intact."""
    require(MODE == 'development', 'Register in development mode; configure production settings separately')
    require(5 <= len(args) <= 7, 'Usage: add NAME PROJECT_PATH PHP HOST DATABASE [DOCUMENT_ROOT] [HTTP_PORT]')
    n, path, version, host, db = args[:5]
    version = version or DEFAULT_PHP_VERSION
    require(n != 'gateway', "Project name 'gateway' is reserved for the shared gateway")
    public = args[5] if len(args) > 5 and args[5] else '.'
    requested = args[6] if len(args) > 6 else ''
    require(valid_name(n) and valid_name(db), 'Project/database names must use lowercase letters, numbers, and underscores (maximum 32 characters)')
    require(version in PHP_VERSIONS['development'], 'Unsupported development PHP version')
    require(valid_host(host), 'Use a hostname without a scheme or port')
    require(public == '.' or relative(public), 'Use a relative document root such as public')
    with lock(n), lock():
        (ROOT / 'projects').mkdir(exist_ok=True)
        registered, used_ports = [], {'8081', '8082'}
        for directory in sorted((ROOT / 'projects').iterdir()):
            example = directory / '.env.example'
            definition = directory / '.env'
            if not definition.is_file():
                definition = example
            if definition.is_file():
                values = dotenv(definition)
                if values.get('WORKSPACE_PROJECT_NAME'):
                    registered.append(values)
                    used_ports.add(values.get('WORKSPACE_HTTP_PORT', ''))
        require(not any(p['WORKSPACE_PROJECT_NAME'] == n for p in registered), f'Project already registered: {n}')
        require(not any(host in (p.get('WORKSPACE_HOSTNAME'), *p.get('WORKSPACE_DOMAIN_ALIASES', '').split()) for p in registered), f'Hostname already registered: {host}')
        settings, _ = infra()
        used_ports.add(settings.get('NGINX_HTTP_PORT', '80'))
        if not requested:
            port = 8083
            while str(port) in used_ports:
                port += 1
            requested = str(port)
        require(requested.isdigit() and 1024 <= int(requested) <= 65535, 'HTTP_PORT must be between 1024 and 65535')
        require(requested not in used_ports, f'HTTP port already assigned: {requested}')
        source = Path(path)
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
        alias = ROOT / 'projects' / n
        new_source = not source.exists() and source == alias.resolve()
        require(new_source or source.is_dir(), f'Project path does not exist: {source}')
        created_dirs = []
        if new_source:
            source.mkdir(mode=0o755)
            source.chmod(0o755)
            created_dirs.append(source)
            current = source
            for part in Path(public).parts:
                current /= part
                current.mkdir(mode=0o755)
                current.chmod(0o755)
                created_dirs.append(current)
        linked = False
        created = []
        env_path = source / '.env'
        example = source / '.env.example'
        original = env_path.read_bytes() if env_path.exists() else None
        try:
            require((source / public).is_dir(), f'Document root does not exist: {source / public}')
            require((source / public).resolve().is_relative_to(source), 'Document root must stay inside the application')
            if source != alias.resolve():
                require(not alias.exists() and not alias.is_symlink(), f'Project directory already exists: {alias}')
                alias.symlink_to(source, target_is_directory=True)
                linked = True
            template = Template((ROOT / 'docker/project.env.example').read_text()).safe_substitute(
                PROJECT_NAME=n, HOSTNAME=host, DATABASE=db, DATABASE_USER=f'{n[:27]}_user')
            base = original.decode() if original is not None else example.read_text() if example.exists() else template
            values = parse_dotenv(base, str(env_path))
            require(not values.get('WORKSPACE_PROJECT_NAME'), f'Project already registered in {env_path}')
            require(valid_name(values.get('DB_DATABASE', db)), 'Invalid existing DB_DATABASE')
            block = registration_settings(n, version, host, public, requested)
            content = base + ('' if base.endswith('\n') else '\n')
            if 'DB_DATABASE' not in values:
                content += f'DB_DATABASE={db}\n'
            env_path.write_text(content + block)
            if original is None:
                env_path.chmod(0o644)  # New registrations use development-only credentials.
            if not example.exists():
                write_new(example, template + block, created)
                example.chmod(0o644)
            if new_source:
                starter = source / public / 'index.php'
                write_new(starter, f'<?php\nheader("Content-Type: text/plain; charset=utf-8");\necho "{n} is running\\n";\necho "PHP version: " . PHP_VERSION . "\\n";\n', created)
                starter.chmod(0o644)
            generate([n])  # The registration transaction already owns the generation lock.
        except BaseException:
            if original is not None:
                env_path.write_bytes(original)
            else:
                env_path.unlink(missing_ok=True)
            for target in reversed(created):
                target.unlink(missing_ok=True)
            if linked:
                alias.unlink()
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass  # Preserve any files created independently during registration.
            raise
        print(f'Added {n}. Open http://localhost:{requested}, or map 127.0.0.1 {host} and use the gateway. Start with make project-up NAME={n}.')


def sync_projects(names):
    """Lock the requested projects before promoting their generated configurations."""
    with ExitStack() as locks:
        for n in sorted(names):
            require(valid_name(n), 'Invalid project name')
            locks.enter_context(lock(n))
        return sync(names)


def main():
    os.umask(0o077)
    require(len(sys.argv) >= 2, 'Provide a stack action')
    action = sys.argv[1]
    args = sys.argv[2:]
    require(MODE in PHP_VERSIONS, 'ENV must be development or production')
    if action in {'doctor', 'setup'}:
        doctor() if action == 'doctor' else setup()
        return
    infra()
    if action == 'add':
        register(args)
        return
    if action == 'list':
        for n in project_names():
            print(n)
        return
    if action == 'gateway-logs':
        gateway_compose('logs', '-f')
        return
    if action == 'gateway-down':
        with lock('gateway'):
            gateway_compose('down')
        return
    if action in {'gateway-up', 'gateway-sync'}:
        with lock('gateway'):
            projects = [project(n) for n in project_names()]
            gateway_generate(projects)
            if action == 'gateway-up':
                gateway_compose('up', '-d', '--wait')
            gateway_connect(projects)
        return
    if action in {'sync', 'up-all', 'down-all'}:
        if action == 'sync' and not args and MODE == 'development':
            with lock():
                prepare_examples()
        names = args or project_names()
        if action == 'sync':
            sync_projects(names)
            print('Validated independent stacks: ' + ', '.join(names))
        else:
            for n in names:
                with lock(n):
                    if action == 'down-all':
                        compose(project(n), 'down')
                    else:
                        up(sync([n])[0])
        return
    require(args and valid_name(args[0]), 'An explicit NAME is required; use up-all/down-all for every project')
    n = args[0]
    require(n != 'gateway', "Project name 'gateway' is reserved for the shared gateway")
    if action == 'compose':
        require(len(args) > 1 and args[1] in {'config', 'ps', 'logs', 'exec', 'images', 'top'},
                'Use project lifecycle commands to build/start/stop; compose supports inspection and exec')
        result = compose(project(n), *args[1:], check=False, profiles=False)
        sys.exit(result.returncode)
    if action in {'project-logs', 'project-ps', 'shell', 'mariadb', 'redis-cli'}:
        p = project(n)
        commands = {'project-logs': ['logs', '-f'], 'project-ps': ['ps'], 'shell': ['exec', 'php', 'sh'],
                    'mariadb': ['exec', 'mariadb', 'sh', '-c', 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot'],
                    'redis-cli': ['exec', 'redis', 'sh', '-c', 'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli']}
        compose(p, *commands[action], profiles=False)
        return
    with lock(n):
        if action in {'scaffold', 'check'}:
            created = []
            if action == 'scaffold':
                with lock():
                    project(n, created)
            else:
                project(n)
        elif action in {'backup', 'restore'}:
            database(action, n, args[1] if len(args) > 1 else '')
        elif action in {'project-up', 'project-build', 'project-restart', 'validate'}:
            p = sync([n])[0]
            if action == 'project-up':
                up(p)
            elif action == 'project-build':
                compose(p, 'build')
            elif action == 'project-restart':
                compose(p, 'restart', profiles=False)
            else:
                print(f'Validated {p["stack"]}')
        elif action == 'project-down':
            compose(project(n), 'down', profiles=False)
        else:
            raise ValueError(f'Unknown action: {action}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Workspace command failed: {error}', file=sys.stderr)
        sys.exit(1)
