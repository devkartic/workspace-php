"""Split workspace/application environments and nested-source registration."""
import shutil
import unittest

import test_workspace


class NestedSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workspace.WorkspaceTests()
        self.fixture.mode = 'production'
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def split(self, name='alpha'):
        project = self.root / 'projects' / name
        source = project / 'src'
        source.mkdir()
        values = (project / '.env').read_text().splitlines()
        workspace = [line for line in values if line.startswith('WORKSPACE_')]
        application = [line for line in values if not line.startswith('WORKSPACE_')]
        (project / '.env').write_text('\n'.join(workspace) + '\nWORKSPACE_SOURCE_ROOT=src\n')
        (source / '.env').write_text('\n'.join(application) + '\n')
        (project / '.env.example').write_bytes((project / '.env').read_bytes())
        (source / '.env.example').write_bytes((source / '.env').read_bytes())
        for directory in ('public', 'storage', 'bootstrap'):
            shutil.move(project / directory, source / directory)
        shutil.rmtree(project / '.docker')
        return project.resolve(), source.resolve()

    def test_nested_mounts_credentials_logs_and_compose_path_base(self):
        project, source = self.split()
        before = [(path, path.read_bytes()) for path in (project / '.env', source / '.env')]
        self.fixture.sync('alpha')
        module = self.fixture.load_module()
        p = module.project('alpha')
        self.assertEqual(p['source'], source)
        self.assertEqual(p['project_dir'], project)
        self.assertEqual(p['app']['DB_DATABASE'], 'app')
        self.assertEqual(p['env']['PROJECT_SOURCE'], str(source))
        self.assertEqual(p['env']['PROJECT_ROOT'], str(project))
        args = module.compose_args(p)
        self.assertEqual(args[args.index('--project-directory') + 1], str(project))
        config = self.fixture.resolved('alpha')
        for service in ('php', 'nginx'):
            mounts = {v['target']: v for v in config['services'][service]['volumes']}
            self.assertEqual(mounts['/var/www/html']['source'], str(source))
            self.assertTrue(mounts['/var/www/html']['read_only'])
        logs = next(v for v in config['services']['php']['volumes'] if v['target'] == '/var/log/project')
        self.assertEqual(logs['source'], str(project / 'logs'))
        self.assertFalse((source / 'logs').exists())
        self.assertEqual(config['services']['nginx']['environment']['DOCUMENT_ROOT'], 'public')
        for path, content in before:
            self.assertEqual(path.read_bytes(), content)

    def test_environment_separation_and_source_escape_are_rejected(self):
        project, source = self.split()
        outer, inner = project / '.env', source / '.env'
        outer_original, inner_original = outer.read_text(), inner.read_text()
        outer.write_text(outer_original + 'DB_PASSWORD=wrong_place\n')
        self.assertIn('parent .env', self.fixture.sync('alpha', ok=False).stderr)
        outer.write_text(outer_original)
        inner.write_text(inner_original + 'WORKSPACE_PHP_VERSION=8.3\n')
        self.assertIn('must not contain WORKSPACE_', self.fixture.sync('alpha', ok=False).stderr)
        inner.write_text(inner_original)
        outer.write_text(outer_original.replace('WORKSPACE_SOURCE_ROOT=src', 'WORKSPACE_SOURCE_ROOT=../beta'))
        self.assertIn('invalid WORKSPACE_SOURCE_ROOT', self.fixture.sync('alpha', ok=False).stderr)
        outer.write_text(outer_original.replace('WORKSPACE_SOURCE_ROOT=src', 'WORKSPACE_SOURCE_ROOT=escaped'))
        (project / 'escaped').symlink_to(self.root / 'projects/beta', target_is_directory=True)
        self.assertIn('inside the project', self.fixture.sync('alpha', ok=False).stderr)

    def test_project_local_mounted_configuration_scaffolding_and_reuse(self):
        project, source = self.split()
        with (project / '.env').open('a') as stream:
            stream.write('WORKSPACE_SOURCE_MODE=mounted\n')
        self.fixture.sync('alpha')
        module = self.fixture.load_module()
        p = module.project('alpha')
        self.assertTrue(p['mounted'])
        self.assertEqual(p['config'], project / '.docker')
        self.assertFalse((self.root / 'docker/projects/alpha').exists())
        config = self.fixture.resolved('alpha')
        for name in ('php', 'nginx'):
            self.assertEqual(config['services'][name]['build']['context'], str(project / '.docker'))
        self.assertNotIn('COPY --chown', (project / '.docker/Dockerfile').read_text())
        credentials = (project / '.docker/infra.production.env').read_bytes()
        self.fixture.sync('alpha')
        self.assertEqual((project / '.docker/infra.production.env').read_bytes(), credentials)
        self.assertNotIn('WORKSPACE_', (source / '.env').read_text())

    def test_invalid_source_mode_is_rejected(self):
        project, _ = self.split()
        with (project / '.env').open('a') as stream:
            stream.write('WORKSPACE_SOURCE_MODE=invalid\n')
        self.assertIn('WORKSPACE_SOURCE_MODE must be mounted', self.fixture.sync('alpha', ok=False).stderr)

    def test_setup_prepares_both_examples_without_overwriting_values(self):
        project, source = self.split()
        self.fixture.mode = 'development'
        (project / '.env').unlink()
        (source / '.env').unlink()
        module = self.fixture.load_module()
        module.prepare_examples()
        self.assertEqual((project / '.env').read_bytes(), (project / '.env.example').read_bytes())
        self.assertEqual((source / '.env').read_bytes(), (source / '.env.example').read_bytes())
        self.assertEqual((source / '.env').stat().st_mode & 0o777, 0o600)
        (source / '.env').write_text((source / '.env').read_text() + 'APP_CUSTOM=keep\n')
        before = (source / '.env').read_bytes()
        module.prepare_examples()
        self.assertEqual((source / '.env').read_bytes(), before)

    def test_nested_registration_and_transactional_failure(self):
        self.fixture.mode = 'development'
        self.fixture.command('make', 'project-add', 'NAME=nested', 'SOURCE_ROOT=src', 'DOCUMENT_ROOT=public')
        project = self.root / 'projects/nested'
        outer = (project / '.env').read_text()
        self.assertNotIn('DB_DATABASE=', outer)
        self.assertIn('WORKSPACE_SOURCE_ROOT=src', outer)
        self.assertNotIn('WORKSPACE_', (project / 'src/.env').read_text())
        self.assertTrue((project / 'src/.env.example').is_file())
        self.assertTrue((project / 'src/public/index.php').is_file())
        self.assertTrue((self.root / 'docker/projects/nested/Dockerfile').is_file())
        self.assertFalse((project / 'src/.docker').exists())
        external = self.root / 'external/failure'
        (external / 'src/public').mkdir(parents=True)
        original = 'APP_NAME=keep\nDB_DATABASE=app\nDB_USERNAME=root\n'
        (external / 'src/.env').write_text(original)
        self.fixture.stack('add', 'failure', str(external), '8.5', 'failure.local', 'app', 'public', '', 'src', ok=False)
        self.assertEqual((external / 'src/.env').read_text(), original)
        self.assertFalse((external / '.env').exists())
        self.assertFalse((external / 'src/.env.example').exists())
        self.assertFalse((self.root / 'projects/failure').exists())
        self.assertFalse((self.root / 'docker/projects/failure').exists())


if __name__ == '__main__':
    unittest.main()
