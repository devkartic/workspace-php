"""Mounted-source contracts and explicit legacy migration; no running stacks changed."""
import json
import shutil
import subprocess
import unittest
from unittest import mock

import test_workspace


class MountedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workspace.WorkspaceTests()
        self.fixture.mode = 'production'
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def mounted(self):
        for n in ('alpha', 'beta'):
            shutil.rmtree(self.root / f'projects/{n}/.docker')
        self.fixture.sync()
        return self.fixture.load_module()

    def test_external_configuration_and_runtime_only_build_context(self):
        module = self.mounted()
        p = module.project('alpha')
        config = self.fixture.resolved('alpha')
        self.assertTrue(p['mounted'])
        for name in ('php', 'nginx'):
            service = config['services'][name]
            self.assertEqual(service['build']['context'], str(p['config']))
            mounts = {v['target']: v for v in service['volumes']}
            self.assertEqual(mounts['/var/www/html']['source'], str(p['source']))
            self.assertTrue(mounts['/var/www/html']['read_only'])
        self.assertTrue(config['services']['php']['read_only'])
        self.assertEqual(config['volumes']['runtime_storage']['name'], p['stack'] + '_runtime_storage')
        self.assertFalse((p['source'] / '.docker').exists())
        self.assertNotIn('COPY --chown', (p['config'] / 'Dockerfile').read_text())
        self.assertNotIn('COPY ${DOCUMENT_ROOT}', (p['config'] / 'nginx.Dockerfile').read_text())
        ignore = (p['config'] / 'Dockerfile.dockerignore').read_text()
        self.assertIn('\ninfra.*.env\n', ignore)
        self.assertIn('\ncompose.override*.yml\n', ignore)
        self.assertEqual((p['config'] / 'infra.production.env').stat().st_mode & 0o777, 0o600)

    def test_mounted_development_and_worker_example(self):
        self.fixture.mode = 'development'
        self.mounted()
        config = self.fixture.resolved('alpha')
        for name in ('php', 'nginx'):
            mount = next(v for v in config['services'][name]['volumes'] if v['target'] == '/var/www/html')
            self.assertEqual(mount.get('read_only', False), name == 'nginx')
        self.fixture.mode = 'production'
        self.fixture.sync('alpha')
        path = self.root / 'docker/projects/alpha'
        shutil.copy2(path / 'compose.override.production.yml.example', path / 'compose.override.production.yml')
        self.fixture.sync('alpha')
        worker = self.fixture.resolved('alpha')['services']['worker']
        self.assertIn('/var/www/html', {v['target'] for v in worker['volumes']})
        (path / 'compose.override.production.yml').write_text('services:\n  worker:\n    image: ${STACK_NAME}-php:${IMAGE_TAG}\n    command: [php, artisan, queue:work]\n')
        self.fixture.sync('alpha', ok=False)

    def test_migration_preserves_secrets_volumes_and_relative_override_paths(self):
        self.fixture.sync('alpha')
        before = self.fixture.resolved('alpha')
        old = self.root / 'projects/alpha/.docker'
        credentials = (old / 'infra.production.env').read_bytes()
        (old / 'compose.override.yml').write_text('services:\n  php:\n    environment:\n      CUSTOM_SETTING: preserved\n')
        env_before = (old.parent / '.env').read_bytes()
        self.fixture.stack('project-migrate-config', 'alpha')
        after = self.fixture.resolved('alpha')
        config = self.root / 'docker/projects/alpha'
        self.assertEqual((config / 'infra.production.env').read_bytes(), credentials)
        self.assertEqual((old.parent / '.env').read_bytes(), env_before)
        self.assertEqual(before['volumes'], after['volumes'])
        self.assertEqual(before['networks'], after['networks'])
        self.assertEqual(before['name'], after['name'])
        self.assertEqual(after['services']['php']['environment']['CUSTOM_SETTING'], 'preserved')
        self.assertTrue(old.exists())
        backups = list((self.root / 'docker/backups/config/alpha').glob('*/infra.production.env'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), credentials)
        self.fixture.stack('project-migrate-config', 'alpha', ok=False)

    def test_migration_rejects_custom_build_and_rolls_back_invalid_override(self):
        self.fixture.sync('alpha')
        old = self.root / 'projects/alpha/.docker'
        dockerfile = old / 'Dockerfile'
        original = dockerfile.read_bytes()
        dockerfile.write_text(dockerfile.read_text() + '\nRUN touch /custom\n')
        self.fixture.stack('project-migrate-config', 'alpha', ok=False)
        self.assertFalse((self.root / 'docker/projects/alpha').exists())
        dockerfile.write_bytes(original)
        runtime = self.fixture.runtime('alpha') / 'compose.json'
        previous = runtime.read_bytes()
        # A legacy build context is invalid after conversion, so migration must roll back.
        (old / 'compose.override.yml').write_text('services:\n  php:\n    build:\n      context: ${PROJECT_SOURCE}\n')
        self.fixture.stack('project-migrate-config', 'alpha', ok=False)
        self.assertFalse((self.root / 'docker/projects/alpha').exists())
        self.assertEqual(runtime.read_bytes(), previous)

    def test_shipped_external_scaffold_does_not_replace_legacy_credentials(self):
        self.fixture.sync('alpha')
        module = self.fixture.load_module()
        old = self.root / 'projects/alpha/.docker'
        credentials = (old / 'infra.production.env').read_bytes()
        target = self.root / 'docker/projects/alpha'
        target.mkdir(parents=True)
        for path in (self.root / 'docker/templates').iterdir():
            if path.is_file():
                (target / path.name).write_text(module.mounted_template(path.name, path.read_text()))
        (target / 'compose.yml').write_text(module.mounted_template('compose.yml', (self.root / 'docker/docker-compose.yml').read_text()))
        self.assertFalse(module.project('alpha')['mounted'])
        self.fixture.sync('alpha')
        self.assertFalse((target / 'infra.production.env').exists())
        # Candidate failure restores the shipped scaffold and the active legacy JSON.
        original = {path.name: path.read_bytes() for path in target.iterdir()}
        runtime = self.fixture.runtime('alpha') / 'compose.json'
        previous = runtime.read_bytes()
        (old / 'compose.override.yml').write_text('services:\n  php:\n    build:\n      context: ${PROJECT_SOURCE}\n')
        self.fixture.stack('project-migrate-config', 'alpha', ok=False)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, original)
        self.assertEqual(runtime.read_bytes(), previous)
        (old / 'compose.override.yml').write_text('services: {}\n')
        self.fixture.stack('project-migrate-config', 'alpha')
        self.assertTrue(module.project('alpha')['mounted'])
        self.assertEqual((target / 'infra.production.env').read_bytes(), credentials)

    def test_laravel_defaults_and_read_only_mount_validation(self):
        module = self.mounted()
        source = self.root / 'projects/alpha'
        (source / 'artisan').write_text('<?php')
        env = source / '.env'
        env.write_text(env.read_text().replace('WORKSPACE_WRITABLE_PATHS=storage bootstrap/cache\n', ''))
        self.fixture.sync('alpha')
        self.assertEqual(module.project('alpha')['writable'], ['storage', 'bootstrap/cache'])
        override = self.root / 'docker/projects/alpha/compose.override.yml'
        override.write_text('services:\n  php:\n    volumes:\n      - ${PROJECT_SOURCE}:/var/www/html:rw\n')
        self.fixture.sync('alpha', ok=False)
        override.write_text('services:\n  php:\n    volumes:\n      - ../beta:/var/www/html:ro\n')
        self.fixture.sync('alpha', ok=False)

    def test_up_reuses_runtime_images_and_builds_only_missing_images(self):
        module = self.mounted()
        p = module.project('alpha')
        config = self.fixture.resolved('alpha')
        image = {'Config': {'Labels': {'io.php-workspace.runtime': 'mounted-production'}}}
        complete = subprocess.CompletedProcess([], 0, json.dumps([image]))
        def compose(project, *args, **kwargs):
            return subprocess.CompletedProcess([], 0, json.dumps(config) if args[:1] == ('config',) else '')
        with mock.patch.object(module, 'edge'), mock.patch.object(module, 'docker', return_value=complete), \
                mock.patch.object(module, 'compose', side_effect=compose) as calls:
            module.up(p)
            self.assertFalse(any(call.args[1:2] == ('build',) for call in calls.call_args_list))
            init = [call.args for call in calls.call_args_list if '/usr/local/bin/prepare-runtime' in call.args]
            self.assertEqual(len(init), 1)
            self.assertEqual(init[0][-2:], ('storage', 'bootstrap/cache'))
        missing = subprocess.CompletedProcess([], 1, '')
        with mock.patch.object(module, 'edge'), mock.patch.object(module, 'docker', side_effect=lambda *args, **kwargs: missing if args[2].endswith('-nginx:current') else complete), \
                mock.patch.object(module, 'compose', side_effect=compose) as calls:
            module.up(p)
            builds = [call.args[1:] for call in calls.call_args_list if call.args[1:2] == ('build',)]
            self.assertEqual(builds, [('build', 'nginx')])
        # The old application image uses the same tag; it must not be reused during migration.
        old = subprocess.CompletedProcess([], 0, json.dumps([{'Config': {'Labels': {}}}]))
        with mock.patch.object(module, 'edge'), mock.patch.object(module, 'docker', return_value=old), \
                mock.patch.object(module, 'compose', side_effect=compose) as calls:
            module.up(p)
            builds = [call.args[2:] for call in calls.call_args_list if call.args[1:2] == ('build',)]
            self.assertEqual(len(builds), 1)
            self.assertEqual(set(builds[0]), {'php', 'nginx'})


if __name__ == '__main__':
    unittest.main()
