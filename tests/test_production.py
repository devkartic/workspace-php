"""Production-specific behavior on independent project stacks."""
import unittest
import test_workspace


class ProductionTests(test_workspace.WorkspaceTests):
    mode = 'production'

    def test_database_and_redis_password_minimum_and_generator(self):
        self.sync('alpha')
        module = self.load_module()
        app_env = self.root / 'projects/alpha/.env'
        private_env = self.root / 'projects/alpha/.docker/infra.production.env'
        generated = module.dotenv(private_env)['MARIADB_ROOT_PASSWORD']
        self.assertRegex(generated, r'^[a-f0-9]{16}$')
        for path, key in ((app_env, 'DB_PASSWORD'), (app_env, 'REDIS_PASSWORD'),
                          (private_env, 'MARIADB_ROOT_PASSWORD')):
            original = path.read_text()
            previous = module.dotenv(path)[key]
            for length in (15, 16, 32, 64):
                with self.subTest(key=key, length=length):
                    path.write_text(original.replace(f'{key}={previous}', f'{key}=' + 'a' * length))
                    if length < 16:
                        with self.assertRaisesRegex(ValueError, 'at least 16 characters'):
                            module.project('alpha')
                    else:
                        module.project('alpha')
            path.write_text(original)
        for invalid in ('REPLACE_WITH_A_SECRET', 'CHANGE_THIS_PASSWORD', 'EXAMPLE_PASSWORD_VALUE',
                        'invalid!password123', 'password with spaces'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module.secret(invalid, 'password')
        existing = private_env.read_bytes()
        self.sync('alpha')
        self.assertEqual(private_env.read_bytes(), existing)

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

    def test_writable_host_mount_exception_is_only_for_php_project_logs(self):
        self.sync('alpha')
        override = self.root / 'projects/alpha/.docker/compose.override.yml'
        for service, source, target in [('php', './public', '/tmp/unsafe'),
                                        ('nginx', './logs', '/var/log/project'),
                                        ('php', './public', '/var/log/project')]:
            with self.subTest(service=service, target=target):
                override.write_text(f'services:\n  {service}:\n    volumes:\n      - {source}:{target}\n')
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
