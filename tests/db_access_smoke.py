#!/usr/bin/env python3
"""Opt-in Docker smoke test using disposable database/network and loopback ports."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import db_access as access


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True, capture_output=True, **kwargs).stdout


def main():
    stack = 'db-access-smoke-' + uuid.uuid4().hex[:10]
    network, database_name = stack + '_backend', stack + '-database'
    password = secrets.token_hex(20)
    # Hold distinct ports until both have been allocated.
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        database_port, adminer_port = first.getsockname()[1], second.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='db-access-smoke-') as directory:
        p = {'name': 'smoke', 'stack': stack, 'runtime': Path(directory),
             'env': {'BUILD_TARGET': 'production', 'DB_DATABASE': 'app', 'DB_USERNAME': 'app_user'}}
        held = None
        try:
            docker('network', 'create', network)
            print('Starting disposable MariaDB...', flush=True)
            database_id = docker('run', '-d', '--name', database_name, '--network', network,
                                 '--network-alias', 'mariadb',
                                 '-e', 'MARIADB_RANDOM_ROOT_PASSWORD=1', '-e', 'MARIADB_DATABASE=app',
                                 '-e', 'MARIADB_USER=app_user', '-e', 'MARIADB_PASSWORD',
                                 '--health-cmd', 'healthcheck.sh --connect --innodb_initialized',
                                 '--health-interval', '2s', '--health-retries', '60', 'mariadb:11.4',
                                 env=dict(os.environ, MARIADB_PASSWORD=password)).strip()
            deadline = time.monotonic() + 180
            while True:
                state = json.loads(docker('inspect', database_id))[0]['State']
                if state.get('Health', {}).get('Status') == 'healthy':
                    break
                if not state['Running'] or time.monotonic() > deadline:
                    raise RuntimeError('Disposable database failed to become healthy')
                time.sleep(1)
            access.enable(p, database_id, database_port, adminer_port)
            access.enable(p, database_id, database_port, adminer_port)
            access.status(p)
            items = access.containers(p)
            assert access.bindings(items) == {('127.0.0.1', database_port), ('127.0.0.1', adminer_port)}
            proxy = next(x['Id'] for x in items if x['Config']['Labels']['com.docker.compose.service'] == 'db-proxy')
            adminer = next(x['Id'] for x in items if x['Config']['Labels']['com.docker.compose.service'] == 'adminer')
            assert b'Adminer' in urlopen(f'http://127.0.0.1:{adminer_port}/', timeout=10).read()
            assert docker('exec', adminer, 'php', '-r', 'echo ini_get("upload_max_filesize");').strip() == '128M'
            print('Importing SQL through the proxy...', flush=True)
            sql = 'CREATE TABLE access_probe (value INT); INSERT INTO access_probe VALUES (42); SELECT value FROM access_probe;'
            result = docker('run', '--rm', '-i', '--network', f'container:{proxy}', '-e', 'MYSQL_PWD',
                            'mariadb:11.4', 'mariadb', '-h127.0.0.1', '-uapp_user', 'app',
                            input=sql, env=dict(os.environ, MYSQL_PWD=password))
            assert '42' in result
            held = socket.create_connection(('127.0.0.1', database_port), timeout=5)
            assert held.recv(4096), 'Missing MariaDB greeting through published endpoint'
            access.disable(p)
            try:
                assert not held.recv(4096), 'Existing tool connection survived disabling'
            except (ConnectionResetError, BrokenPipeError):
                pass
            for port in (database_port, adminer_port):
                try:
                    connection = socket.create_connection(('127.0.0.1', port), timeout=2)
                except OSError:
                    continue
                connection.close()
                raise AssertionError(f'Disabled endpoint {port} is still reachable')
            # Application-equivalent access continues directly on the backend.
            result = docker('exec', '-e', 'MYSQL_PWD', database_id, 'mariadb', '-h127.0.0.1',
                            '-uapp_user', 'app', '-e', 'SELECT value FROM access_probe',
                            env=dict(os.environ, MYSQL_PWD=password))
            assert '42' in result
            access.disable(p)
            print('PASS: imports, Adminer, repeat commands, connection revocation, and continued database access.', flush=True)
        finally:
            if held:
                held.close()
            try:
                access.disable(p)
            finally:
                subprocess.run(['docker', 'rm', '-fv', database_name], capture_output=True)
                subprocess.run(['docker', 'network', 'rm', network], capture_output=True)


if __name__ == '__main__':
    main()
