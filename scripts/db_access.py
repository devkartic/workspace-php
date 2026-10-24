"""Temporary, project-owned database administration endpoints (never public)."""
import json
import os
import socket
import subprocess


PROXY_CONFIG = '''global
    log stdout format raw local0
defaults
    mode tcp
    timeout connect 5s
    timeout client 1h
    timeout server 1h
resolvers docker
    nameserver dns 127.0.0.11:53
    hold valid 10s
frontend database
    bind :3306
    default_backend database
backend database
    server mariadb mariadb:3306 check resolvers docker init-addr libc,none
'''
UPLOAD_CONFIG = 'upload_max_filesize=128M\npost_max_size=130M\nmax_execution_time=300\nmemory_limit=256M\n'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*args, capture=False):
    return subprocess.run(['docker', *args], check=True, text=True, capture_output=capture)


def path(p):
    return p['runtime'] / 'db-access.json'


def port(value):
    text = str(value)
    require(text.isascii() and text.isdigit() and 1024 <= int(text) <= 65535,
            'Database access ports must be integers from 1024 to 65535')
    return int(text)


def config(p, database_port, adminer_port):
    database_port, adminer_port = port(database_port), port(adminer_port)
    require(database_port != adminer_port, 'DB_ACCESS_PORT and ADMINER_PORT must differ')
    common = {'restart': 'no', 'networks': ['backend'],
              'labels': {'io.php-workspace.db-access': p['stack']},
              'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
    return {
        'name': p['stack'] + '-access',
        'services': {
            'db-proxy': {**common, 'image': 'haproxy:3.0-alpine', 'mem_limit': '64m', 'cpus': '0.25',
                         'ports': [f'127.0.0.1:{database_port}:3306'],
                         'configs': [{'source': 'proxy', 'target': '/usr/local/etc/haproxy/haproxy.cfg'}],
                         'healthcheck': {'test': ['CMD', 'haproxy', '-c', '-f', '/usr/local/etc/haproxy/haproxy.cfg'],
                                         'interval': '5s', 'timeout': '3s', 'retries': 5}},
            'adminer': {**common, 'image': 'adminer:5-standalone', 'mem_limit': '384m', 'cpus': '0.5',
                        'environment': {'ADMINER_DEFAULT_SERVER': 'mariadb'},
                        'ports': [f'127.0.0.1:{adminer_port}:8080'],
                        'configs': [{'source': 'uploads', 'target': '/usr/local/etc/php/conf.d/zz-workspace.ini'}],
                        'healthcheck': {'test': ['CMD', 'php', '-r',
                                               'exit(@file_get_contents("http://127.0.0.1:8080/") === false ? 1 : 0);'],
                                        'interval': '5s', 'timeout': '3s', 'retries': 5}},
        },
        'networks': {'backend': {'external': True, 'name': p['stack'] + '_backend'}},
        'configs': {'proxy': {'content': PROXY_CONFIG}, 'uploads': {'content': UPLOAD_CONFIG}},
    }


def compose(p, *args, file=None, capture=False):
    # Intentionally excludes application overrides, dotenv files, and COMPOSE_* controls.
    environment = {k: v for k, v in os.environ.items() if not k.startswith('COMPOSE_')}
    return subprocess.run(['docker', 'compose', '--project-directory', str(p['runtime']),
                           '-p', p['stack'] + '-access', '-f', str(file or path(p)), *args],
                          env=environment, check=True, text=True, capture_output=capture)


def validate(p, resolved, database_port, adminer_port):
    """Validate the dedicated stack without relaxing application isolation checks."""
    expected = config(p, database_port, adminer_port)
    require(resolved['name'] == expected['name'], 'Database access Compose identity changed')
    require(set(resolved['services']) == {'db-proxy', 'adminer'}, 'Unexpected database access service')
    network = resolved['networks']
    require(set(network) == {'backend'} and network['backend'].get('external')
            and network['backend']['name'] == p['stack'] + '_backend', 'Invalid database access network')
    require(not resolved.get('volumes') and not resolved.get('secrets'), 'Unexpected access volumes or secrets')
    require(set(resolved.get('configs', {})) == {'proxy', 'uploads'}, 'Invalid access configuration')
    for key, value in expected['configs'].items():
        require(resolved['configs'][key].get('content') == value['content'], 'Access configuration changed')
    for name, service in resolved['services'].items():
        original = expected['services'][name]
        require(service.get('image') == original['image'] and service.get('restart') == 'no', 'Invalid access image/restart policy')
        require(set(service.get('networks', {})) == {'backend'}, 'Invalid access service network')
        require(not any(service.get(k) for k in ('privileged', 'network_mode', 'pid', 'ipc', 'devices',
                    'cap_add', 'volumes', 'build', 'container_name', 'use_api_socket', 'env_file',
                    'links', 'external_links', 'volumes_from', 'uts')), 'Unsafe access service settings')
        require(service.get('environment', {}) == original.get('environment', {}), 'Unexpected access environment')
        require(service.get('configs') == original['configs'], 'Unexpected access mounts')
        ports = service.get('ports', [])
        published, target = (database_port, 3306) if name == 'db-proxy' else (adminer_port, 8080)
        require(len(ports) == 1 and ports[0].get('host_ip') == '127.0.0.1'
                and str(ports[0].get('published')) == str(published)
                and ports[0].get('target') == target and ports[0].get('protocol') == 'tcp',
                'Database access must bind only its approved localhost ports')


def containers(p):
    ids = run('ps', '-aq', '--filter', f'label=com.docker.compose.project={p["stack"]}-access', capture=True).stdout.split()
    if not ids:
        return []
    items = json.loads(run('inspect', *ids, capture=True).stdout)
    require(all(x['Config']['Labels'].get('io.php-workspace.db-access') == p['stack'] for x in items),
            'Database access stack contains containers with unexpected ownership')
    return items


def bindings(items):
    return {(entry['HostIp'], int(entry['HostPort']))
            for item in items for entries in item['NetworkSettings'].get('Ports', {}).values()
            for entry in (entries or [])}


def disable(p):
    items = containers(p)
    if items and path(p).exists():
        compose(p, 'down', '--remove-orphans', '--timeout', '5')
    elif items:
        run('rm', '-f', *(item['Id'] for item in items))
    require(not containers(p), 'Database access containers remain; retry db-access-disable')
    print(f'{p["name"]}: database tool access disabled. Application and SSH backup/restore remain available.')


def instructions(p, database_port, adminer_port):
    print(f'{p["name"]}: private database access enabled (until disabled or the host restarts).')
    print('On your computer, keep this SSH tunnel running (replace SSH_USER and VPS_IP):')
    print(f'  ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:{adminer_port}:127.0.0.1:{adminer_port} SSH_USER@VPS_IP')
    print(f'Open Adminer: http://127.0.0.1:{adminer_port}/ (server: mariadb).')
    print(f'Sequel Pro SSH tab: MySQL host 127.0.0.1, port {database_port}; SSH host VPS_IP, your SSH user/key.')
    print(f'Database: {p["env"]["DB_DATABASE"]}; username: {p["env"]["DB_USERNAME"]}; password: DB_PASSWORD from application .env.')
    print('Import using Sequel Pro or Adminer (128 MB upload limit); use make restore for larger trusted SQL files.')
    print(f'To close access: make db-access-disable ENV={p["env"]["BUILD_TARGET"]} NAME={p["name"]}')


def enable(p, database_id, database_port=13306, adminer_port=18080):
    database_port, adminer_port = port(database_port), port(adminer_port)
    wanted = config(p, database_port, adminer_port)
    require(database_id, 'Project database is not running; start the project first')
    database = json.loads(run('inspect', database_id, capture=True).stdout)[0]
    require(database['State'].get('Running') and database['State'].get('Health', {}).get('Status') == 'healthy',
            'Project database is not healthy; check project-ps and project-logs')
    require(p['stack'] + '_backend' in database['NetworkSettings']['Networks'], 'Database backend network mismatch')
    owned = bindings(containers(p))
    require(owned <= {('127.0.0.1', database_port), ('127.0.0.1', adminer_port)},
            'Access already uses different ports; disable it before changing ports')
    for number in (database_port, adminer_port):
        if ('127.0.0.1', number) not in owned:
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', number))
                except OSError as error:
                    raise ValueError(f'Local port {number} is unavailable; choose DB_ACCESS_PORT / ADMINER_PORT') from error
    p['runtime'].mkdir(parents=True, exist_ok=True)
    candidate = path(p).with_suffix('.pending.json')
    try:
        candidate.write_text(json.dumps(wanted, indent=2) + '\n')
        candidate.chmod(0o600)
        resolved = json.loads(compose(p, 'config', '--format', 'json', file=candidate, capture=True).stdout)
        validate(p, resolved, database_port, adminer_port)
        candidate.replace(path(p))
        try:
            compose(p, 'up', '-d', '--wait', '--wait-timeout', '90', '--remove-orphans')
        except BaseException:
            disable(p)  # Close any endpoint that started before a pull/bind/health failure.
            raise
    finally:
        candidate.unlink(missing_ok=True)
    instructions(p, database_port, adminer_port)


def status(p):
    items = containers(p)
    if not items:
        print(f'{p["name"]}: database tool access disabled')
        return
    for item in items:
        state = item['State']
        health = state.get('Health', {}).get('Status', 'no healthcheck')
        print(f'{item["Config"]["Labels"]["com.docker.compose.service"]}: {state["Status"]} ({health})')
    for host, number in sorted(bindings(items)):
        print(f'  {host}:{number}')
