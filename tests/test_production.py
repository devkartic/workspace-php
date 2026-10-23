"""Production-specific behavior on independent project stacks."""
import unittest
import test_workspace


class ProductionTests(test_workspace.WorkspaceTests):
    mode = 'production'

    def test_production_has_no_host_ports_and_immutable_source(self):
        self.sync()
        config = self.resolved('alpha')
        self.assertFalse(any(service.get('ports') for service in config['services'].values()))
        php = config['services']['php']
        self.assertTrue(php['read_only'])
        self.assertEqual(php['build']['target'], 'production')
        binds = {m['target']: m for m in php['volumes'] if m['type'] == 'bind'}
        self.assertNotIn('/var/www/html', binds)
        self.assertTrue(binds['/var/www/html/.env']['read_only'])
        self.assertIn('runtime_storage', config['volumes'])
        ignore = (self.root / 'projects/alpha/.docker/Dockerfile.dockerignore').read_text()
        self.assertIn('.env', ignore)
        self.assertIn('.docker/infra.*.env', ignore)

    def test_production_validation_blocks_unsafe_settings(self):
        original = (self.root / 'projects/alpha/.env').read_text()
        for old, new in [('APP_DEBUG=false', 'APP_DEBUG=true'),
                         ('WORKSPACE_DOCUMENT_ROOT=public', 'WORKSPACE_DOCUMENT_ROOT=.'),
                         ('WORKSPACE_PHP_VERSION=8.4', 'WORKSPACE_PHP_VERSION=7.4'),
                         ('WORKSPACE_HOSTNAME=alpha.example.com', 'WORKSPACE_HOSTNAME=alpha.local'),
                         ('DB_USERNAME=app_user', 'DB_USERNAME=root')]:
            with self.subTest(new=new):
                (self.root / 'projects/alpha/.env').write_text(original.replace(old, new))
                self.sync('alpha', ok=False)
        (self.root / 'projects/alpha/.env').write_text(original)
        self.sync('alpha')
        (self.root / 'projects/alpha/.docker/compose.override.yml').write_text('services:\n  redis:\n    ports: ["6379:6379"]\n')
        self.sync('alpha', ok=False)

    def test_no_production_example_bootstrap(self):
        extra = self.root / 'projects/example'
        extra.mkdir()
        (extra / '.env.example').write_text('WORKSPACE_PROJECT_NAME=example\n')
        self.sync()
        self.assertFalse((extra / '.env').exists())
        self.assertFalse((extra / '.docker').exists())

    def test_existing_external_volumes_are_adopted_explicitly(self):
        self.change('alpha', 'WORKSPACE_WRITABLE_PATHS=storage bootstrap/cache', 'WORKSPACE_WRITABLE_PATHS=')
        with (self.root / 'projects/alpha/.env').open('a') as stream:
            stream.write('WORKSPACE_STORAGE_VOLUME=old_alpha_storage\nWORKSPACE_CACHE_VOLUME=old_alpha_cache\n')
        self.sync('alpha')
        volumes = self.resolved('alpha')['volumes']
        self.assertEqual(volumes['runtime_storage']['name'], 'old_alpha_storage')
        self.assertTrue(volumes['runtime_storage']['external'])

    def test_adopted_volume_cannot_be_shared_by_separate_stacks(self):
        for n in ('alpha', 'beta'):
            self.change(n, 'WORKSPACE_WRITABLE_PATHS=storage bootstrap/cache', 'WORKSPACE_WRITABLE_PATHS=')
            with (self.root / f'projects/{n}/.env').open('a') as stream:
                stream.write('WORKSPACE_STORAGE_VOLUME=old_storage\nWORKSPACE_CACHE_VOLUME=old_cache\n')
        self.sync('alpha')
        self.assertIn('reserved for another', self.sync('beta', ok=False).stderr)

    def test_unselected_project_cannot_be_started(self):
        path = self.root / 'docker/.env.production'
        path.write_text(path.read_text().replace('alpha beta', 'alpha'))
        self.assertIn('not selected', self.sync('beta', ok=False).stderr)


if __name__ == '__main__':
    unittest.main()
