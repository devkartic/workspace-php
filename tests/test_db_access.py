"""Maintenance endpoint regressions; no real database or containers are modified."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import db_access as access


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = {'name': 'alpha', 'stack': 'test-production-alpha', 'runtime': Path(self.temp.name),
                  'env': {'BUILD_TARGET': 'production', 'DB_DATABASE': 'app', 'DB_USERNAME': 'app_user',
                          'DB_PASSWORD': 'never-print-this'}}

    def resolved(self, p=None):
        p = p or self.p
        candidate = p['runtime'] / 'candidate.json'
        candidate.write_text(json.dumps(access.config(p, 13306, 18080)))
        return json.loads(access.compose(p, 'config', '--format', 'json', file=candidate, capture=True).stdout)

    def test_real_compose_configuration_is_private_and_project_owned(self):
        for mode in ('production', 'development'):
            p = dict(self.p, stack=f'test-{mode}-alpha')
            resolved = self.resolved(p)
            access.validate(p, resolved, 13306, 18080)
            self.assertNotIn('never-print-this', json.dumps(resolved))
            self.assertEqual(resolved['networks']['backend']['name'], p['stack'] + '_backend')

    def test_maintenance_validation_rejects_unsafe_configuration(self):
        original = self.resolved()
        edits = [lambda c: c['services']['adminer']['ports'][0].update(host_ip='0.0.0.0'),
                 lambda c: c['services']['adminer'].update(privileged=True),
                 lambda c: c['services']['adminer'].update(volumes=[{'source': '/tmp', 'target': '/tmp'}]),
                 lambda c: c['networks']['backend'].update(name='other-project_backend'),
                 lambda c: c['services']['db-proxy'].update(network_mode='host'),
                 lambda c: c['services']['adminer'].update(environment={'DB_PASSWORD': 'secret'})]
        for edit in edits:
            config = copy.deepcopy(original)
            edit(config)
            with self.assertRaises(ValueError):
                access.validate(self.p, config, 13306, 18080)

    def test_ports_reject_invalid_duplicate_and_occupied_values(self):
        for value in ('0', '1023', '65536', '-1', 'abc', '１２３４'):
            with self.assertRaises(ValueError):
                access.config(self.p, value, 18080)
        with self.assertRaises(ValueError):
            access.config(self.p, 18080, 18080)
        with mock.patch.object(access, 'run') as run, mock.patch.object(access, 'containers', return_value=[]), \
                mock.patch.object(access.socket, 'socket') as socket:
            run.return_value.stdout = json.dumps([self.database()])
            socket.return_value.__enter__.return_value.bind.side_effect = OSError('in use')
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                access.enable(self.p, 'database')
        self.assertFalse(access.path(self.p).exists())

    def database(self):
        return {'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'NetworkSettings': {'Networks': {self.p['stack'] + '_backend': {}}}}

    def test_enable_requires_healthy_database(self):
        with self.assertRaisesRegex(ValueError, 'not running'):
            access.enable(self.p, '')
        database = self.database()
        database['State']['Health']['Status'] = 'unhealthy'
        with mock.patch.object(access, 'run') as run:
            run.return_value.stdout = json.dumps([database])
            with self.assertRaisesRegex(ValueError, 'not healthy'):
                access.enable(self.p, 'database')

    def test_partial_start_failure_closes_endpoints(self):
        resolved = self.resolved()
        with mock.patch.object(access, 'run') as run, mock.patch.object(access, 'containers', return_value=[]), \
                mock.patch.object(access, 'compose') as compose, mock.patch.object(access, 'disable') as disable, \
                mock.patch.object(access.socket, 'socket'):
            run.return_value.stdout = json.dumps([self.database()])
            compose.side_effect = [subprocess.CompletedProcess([], 0, json.dumps(resolved)),
                                   subprocess.CalledProcessError(1, 'up')]
            with self.assertRaises(subprocess.CalledProcessError):
                access.enable(self.p, 'database')
            disable.assert_called_once_with(self.p)
        self.assertFalse(access.path(self.p).with_suffix('.pending.json').exists())

    def test_repeated_enable_reuses_owned_ports_and_never_prints_password(self):
        resolved = self.resolved()
        item = {'NetworkSettings': {'Ports': {'3306/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '13306'}],
                                             '8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]}}}
        with mock.patch.object(access, 'run') as run, mock.patch.object(access, 'containers', return_value=[item]), \
                mock.patch.object(access, 'compose') as compose, mock.patch.object(access.socket, 'socket') as socket:
            run.return_value.stdout = json.dumps([self.database()])
            compose.return_value.stdout = json.dumps(resolved)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                access.enable(self.p, 'database')
                access.enable(self.p, 'database')
            socket.assert_not_called()
            self.assertNotIn('never-print-this', output.getvalue())
            self.assertIn('SSH tab', output.getvalue())
            with self.assertRaisesRegex(ValueError, 'different ports'):
                access.enable(self.p, 'database', 13307, 18081)

    def test_status_and_disable_are_idempotent_without_configuration(self):
        with mock.patch.object(access, 'containers', return_value=[]), mock.patch.object(access, 'compose') as compose:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                access.status(self.p)
                access.disable(self.p)
                access.disable(self.p)
            compose.assert_not_called()
            self.assertIn('disabled', output.getvalue())


if __name__ == '__main__':
    unittest.main()
