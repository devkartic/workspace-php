"""Independent stack regressions; no containers or real project data are changed."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    mode = 'development'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='isolated-workspace-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / 'scripts', self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(SOURCE / 'docker/templates', self.root / 'docker/templates')
        for item in ('Makefile', 'docker/docker-compose.yml', 'docker/.env.example', 'docker/.env.production.example', 'docker/project.env.example'):
            shutil.copy2(SOURCE / item, self.root / item)
        (self.root / 'docker/.env').write_text('COMPOSE_PROJECT_NAME=isolated-test\nNGINX_HTTP_PORT=80\n')
        (self.root / 'docker/.env.production').write_text('COMPOSE_PROJECT_NAME=isolated-test\nPRODUCTION_PROJECTS=alpha beta\nACME_EMAIL=ops@example.com\n')
        for n, port in (('alpha', 8091), ('beta', 8092)):
            app = self.root / f'projects/{n}'
            for directory in ('public', 'storage/app/public', 'bootstrap/cache'):
                (app / directory).mkdir(parents=True, exist_ok=True)
            (app / 'public/index.php').write_text(f'<?php echo "{n}";')
            # Identical database names/users are safe because servers are independent.
            (app / '.env').write_text(
                f'WORKSPACE_PROJECT_NAME={n}\nWORKSPACE_PHP_VERSION=8.4\n'
                f'WORKSPACE_HOSTNAME={n}.example.com\nWORKSPACE_DOCUMENT_ROOT=public\n'
                f'WORKSPACE_HTTP_PORT={port}\nWORKSPACE_WRITABLE_PATHS=storage bootstrap/cache\n'
                f'APP_ENV=production\nAPP_DEBUG=false\nAPP_URL=https://{n}.example.com\n'
                f'DB_HOST=mariadb\nDB_PORT=3306\nDB_DATABASE=app\nDB_USERNAME=app_user\nDB_PASSWORD={n * 16}\n'
                f'REDIS_HOST=redis\nREDIS_PASSWORD={n * 16}\nREDIS_PREFIX={n}:\n')

    def command(self, *args, ok=True, env=None):
        environment = dict(os.environ, WORKSPACE_ENV=self.mode)
        environment.update(env or {})
        result = subprocess.run(args, cwd=self.root, env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def stack(self, *args, **kwargs):
        return self.command('python3', 'scripts/stacks.py', *args, **kwargs)

    def sync(self, *names, **kwargs):
        return self.stack('sync', *names, **kwargs)

    def resolved(self, n):
        return json.loads(self.stack('compose', n, 'config', '--format', 'json').stdout)

    def change(self, n, old, new):
        path = self.root / f'projects/{n}/.env'
        path.write_text(path.read_text().replace(old, new))

    def runtime(self, n):
        return self.root / 'docker/stacks' / self.mode / n

    def load_module(self):
        spec = importlib.util.spec_from_file_location('workspace_under_test', self.root / 'scripts/stacks.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.MODE = self.mode
        return module

    def test_domain_aliases_redirect_with_uri_and_method_preservation(self):
        with (self.root / 'projects/alpha/.env').open('a') as stream:
            stream.write('WORKSPACE_DOMAIN_ALIASES=www.alpha.example.com old.alpha.example.com\n')
        self.sync('alpha')
        module = self.load_module()
        text = module.gateway_caddyfile([module.project('alpha')], module.infra()[0])
        scheme = 'https' if self.mode == 'production' else 'http'
        self.assertIn(f'redir {scheme}://alpha.example.com{{uri}} 308', text)
        self.assertIn('www.alpha.example.com {', text)
        self.assertIn('old.alpha.example.com {', text)
        self.assertEqual(text.count('reverse_proxy'), 1)
        if self.mode == 'development':
            settings = dict(module.infra()[0], NGINX_HTTP_PORT='8080')
            text = module.gateway_caddyfile([module.project('alpha')], settings)
            self.assertIn('redir http://alpha.example.com:8080{uri} 308', text)

    def test_invalid_domain_aliases_are_rejected(self):
        path = self.root / 'projects/alpha/.env'
        original = path.read_text()
        for alias in ('https://www.example.com', '*.example.com', 'alpha.example.com',
                      'www.example.com www.example.com', 'www.example.com:443', 'UPPER.example.com', '127.0.0.1'):
            with self.subTest(alias=alias):
                path.write_text(original + f'WORKSPACE_DOMAIN_ALIASES={alias}\n')
                self.sync('alpha', ok=False)

    def test_gateway_failure_preserves_both_active_configuration_files(self):
        self.sync()
        module = self.load_module()
        projects = [module.project(n) for n in ('alpha', 'beta')]
        with mock.patch.object(module, 'gateway_compose'):
            module.gateway_generate(projects)
        dest = module.gateway_paths()[2]
        files = [dest / 'compose.json', dest / 'caddy/Caddyfile']
        before = [p.read_bytes() for p in files]
        projects[0]['aliases'] = ['beta.example.com']
        with self.assertRaisesRegex(ValueError, 'Duplicate project domain'):
            module.gateway_generate(projects)
        self.assertEqual([p.read_bytes() for p in files], before)
        projects[0]['aliases'] = ['www.alpha.example.com']
        with mock.patch.object(module, 'gateway_compose', side_effect=subprocess.CalledProcessError(1, ['docker'])):
            with self.assertRaises(subprocess.CalledProcessError):
                module.gateway_generate(projects)
        self.assertEqual([p.read_bytes() for p in files], before)
        self.assertFalse((dest / 'compose.pending.json').exists())
        self.assertFalse((dest / 'caddy/Caddyfile.pending').exists())

    def test_canonical_alias_collisions_preserve_project_configuration(self):
        self.sync()
        before = (self.runtime('alpha') / 'compose.json').read_bytes()
        with (self.root / 'projects/alpha/.env').open('a') as stream:
            stream.write('WORKSPACE_DOMAIN_ALIASES=beta.example.com\n')
        self.sync(ok=False)
        self.assertEqual((self.runtime('alpha') / 'compose.json').read_bytes(), before)

    def test_resource_defaults_and_existing_limits_are_preserved(self):
        self.sync('alpha')
        expected = 128 if self.mode == 'production' else 256
        self.assertEqual(int(self.resolved('alpha')['services']['nginx']['mem_limit']), expected * 1024 * 1024)
        file = self.root / f'projects/alpha/.docker/infra.{self.mode}.env'
        file.write_text(file.read_text().replace(f'NGINX_MEMORY={expected}m', 'NGINX_MEMORY=192m'))
        self.sync('alpha')
        self.assertEqual(int(self.resolved('alpha')['services']['nginx']['mem_limit']), 192 * 1024 * 1024)

    def test_independent_images_networks_and_volumes(self):
        self.sync()
        a, b = self.resolved('alpha'), self.resolved('beta')
        self.assertNotEqual(a['name'], b['name'])
        for service in ('php', 'nginx'):
            self.assertNotEqual(a['services'][service]['image'], b['services'][service]['image'])
            self.assertIn('/projects/alpha', a['services'][service]['build']['context'])
        self.assertTrue(set(x['name'] for x in a['networks'].values()).isdisjoint(x['name'] for x in b['networks'].values()))
        self.assertTrue(set(x['name'] for x in a['volumes'].values()).isdisjoint(x['name'] for x in b['volumes'].values()))
        for service in ('php', 'mariadb', 'redis'):
            self.assertEqual(set(a['services'][service]['networks']), {'default'})
        self.assertEqual(set(a['services']['nginx']['networks']), {'default', 'edge'})
        self.assertNotEqual(a['services']['mariadb']['environment']['MARIADB_ROOT_PASSWORD'], b['services']['mariadb']['environment']['MARIADB_ROOT_PASSWORD'])
        self.assertNotEqual(a['services']['redis']['environment']['REDIS_PASSWORD'], b['services']['redis']['environment']['REDIS_PASSWORD'])

    def test_scaffold_and_application_values_are_preserved(self):
        app = self.root / 'projects/alpha'
        before = (app / '.env').read_bytes()
        self.sync()
        dockerfile = app / '.docker/Dockerfile'
        dockerfile.write_text(dockerfile.read_text() + '\n# custom alpha extension\n')
        saved = dockerfile.read_bytes()
        self.sync('alpha')
        self.assertEqual(dockerfile.read_bytes(), saved)
        self.assertEqual((app / '.env').read_bytes(), before)
        self.assertNotIn('custom alpha', (self.root / 'projects/beta/.docker/Dockerfile').read_text())
        self.assertEqual((app / f'.docker/infra.{self.mode}.env').stat().st_mode & 0o777, 0o600)

    def test_project_override_is_included_only_in_its_stack(self):
        self.sync()
        (self.root / 'projects/alpha/.docker/compose.override.yml').write_text('''services:
  worker:
    image: ${STACK_NAME}-php:${IMAGE_TAG}
    command: php -v
''')
        self.sync('alpha')
        a, b = self.resolved('alpha'), self.resolved('beta')
        self.assertIn('worker', a['services'])
        self.assertNotIn('worker', b['services'])
        self.assertEqual(set(a['services']['worker']['networks']), {'default'})
        optional = self.root / f'projects/alpha/.docker/compose.override.{self.mode}.yml'
        optional.write_text('services:\n  worker:\n    environment:\n      PROJECT_ONLY: yes\n')
        self.sync('alpha')
        self.assertEqual(self.resolved('alpha')['services']['worker']['environment']['PROJECT_ONLY'], 'yes')

    def test_invalid_override_preserves_prior_runtime(self):
        self.sync('alpha')
        before = {p.name: p.read_bytes() for p in self.runtime('alpha').iterdir() if p.is_file()}
        (self.root / 'projects/alpha/.docker/compose.override.yml').write_text('services:\n  redis:\n    network_mode: host\n')
        self.sync('alpha', ok=False)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.runtime('alpha').iterdir() if p.is_file()})
        self.assertFalse((self.root / 'docker/.workspace-lock').exists())

    def test_other_project_invalid_settings_do_not_block_target(self):
        self.sync()
        beta_before = (self.runtime('beta') / 'compose.json').read_bytes()
        self.change('beta', 'WORKSPACE_PHP_VERSION=8.4', 'WORKSPACE_PHP_VERSION=9.9')
        self.sync('alpha')
        self.assertEqual((self.runtime('beta') / 'compose.json').read_bytes(), beta_before)

    def test_lifecycle_requires_explicit_project(self):
        for action in ('project-up', 'project-down', 'project-build', 'project-restart', 'project-logs', 'backup', 'restore'):
            self.assertIn('NAME', self.stack(action, '', ok=False).stderr)
        for target in ('up', 'down', 'build', 'restart'):
            self.command('make', target, f'ENV={self.mode}', ok=False)

    def test_shell_values_do_not_override_credentials_or_identity(self):
        self.sync('alpha')
        result = self.stack('compose', 'alpha', 'config', '--format', 'json',
                            env={'MARIADB_ROOT_PASSWORD': 'incorrect', 'STACK_NAME': 'wrong', 'COMPOSE_PROJECT_NAME': 'wrong', 'REDIS_PASSWORD': 'wrong'})
        config = json.loads(result.stdout)
        self.assertEqual(config['name'], f'isolated-test-{self.mode}-alpha')
        self.assertNotEqual(config['services']['mariadb']['environment']['MARIADB_ROOT_PASSWORD'], 'incorrect')
        self.assertEqual(config['services']['redis']['environment']['REDIS_PASSWORD'], 'alpha' * 16)

    def test_application_dotenv_cannot_redirect_docker_transport(self):
        with (self.root / 'projects/alpha/.env').open('a') as stream:
            stream.write('DOCKER_HOST=tcp://invalid.example:2375\nCOMPOSE_PROFILES=wrong\nPATH=/invalid\n')
        self.sync('alpha')
        self.assertEqual(self.resolved('alpha')['name'], f'isolated-test-{self.mode}-alpha')

    def test_gateway_identity_is_reserved_for_shared_routing(self):
        result = self.stack('validate', 'gateway', ok=False)
        self.assertIn('reserved', result.stderr)
        self.assertFalse((self.root / 'docker/stacks' / self.mode / 'gateway').exists())

    def test_generation_lock(self):
        (self.root / 'docker/.workspace-lock').mkdir()
        self.assertIn('busy', self.sync('alpha', ok=False).stderr)

    def test_no_shell_execution_and_external_sources_supported(self):
        app = self.root / 'projects/alpha'
        with (app / '.env').open('a') as stream:
            stream.write('APP_SECRET=$(touch should-not-exist)\n')
        external = self.root / "external/customer's app"
        external.parent.mkdir()
        app.rename(external)
        app.symlink_to(external, target_is_directory=True)
        self.sync('alpha')
        self.assertFalse((self.root / 'should-not-exist').exists())
        self.assertEqual(Path(self.resolved('alpha')['services']['php']['build']['context']).resolve(), external.resolve())

    def test_override_cannot_borrow_other_project_networks_or_mounts(self):
        self.sync('alpha')
        override = self.root / 'projects/alpha/.docker/compose.override.yml'
        for content in (
            'services:\n  php:\n    privileged: true\n',
            'networks:\n  default:\n    name: another_project_backend\n',
            'services:\n  redis:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n',
            'services:\n  redis:\n    volumes_from: ["container:other_project"]\n',
            'services:\n  worker:\n    image: alpine:3\n    networks: [edge]\n',
        ):
            with self.subTest(content=content):
                override.write_text(content)
                self.sync('alpha', ok=False)

    def test_target_generation_respects_other_project_deploy_lock(self):
        self.sync()
        other_lock = self.root / f'docker/.stack-{self.mode}-beta.lock'
        other_lock.mkdir()
        self.sync('alpha')
        self.sync('beta', ok=False)

    def test_backup_failure_never_publishes_partial_dump(self):
        self.sync('alpha')
        folder = self.root / 'bin'
        folder.mkdir()
        docker = folder / 'docker'
        docker.write_text('#!/bin/sh\nprintf "partial SQL"\nexit 1\n')
        docker.chmod(0o755)
        environment = {'PATH': str(folder) + os.pathsep + os.environ['PATH']}
        self.stack('backup', 'alpha', ok=False, env=environment)
        backup = self.root / 'docker/backups' / self.mode / 'alpha'
        self.assertEqual(list(backup.iterdir()), [])
        self.stack('restore', 'alpha', '', ok=False, env=environment)
        docker.write_text('#!/bin/sh\nprintf "SELECT 1;\\n"\n')
        output = Path(self.stack('backup', 'alpha', env=environment).stdout.strip())
        self.assertEqual(output.read_text(), 'SELECT 1;\n')
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_registration_preserves_source_and_environment(self):
        if self.mode != 'development':
            self.skipTest('Registration uses development mode')
        source = self.root / 'external/new app'
        source.mkdir(parents=True)
        original = 'APP_SECRET=keep_me\nDB_DATABASE=existing\n'
        (source / '.env').write_text(original)
        (source / 'index.php').write_text('<?php echo "keep";')
        self.stack('add', 'registered', str(source), '8.4', 'registered.local', 'registered')
        self.assertTrue((source / '.env').read_text().startswith(original))
        self.assertEqual((source / 'index.php').read_text(), '<?php echo "keep";')
        self.assertTrue((self.root / 'projects/registered').is_symlink())
        self.assertTrue((source / '.docker/Dockerfile').is_file())

    def test_failed_registration_restores_environment(self):
        if self.mode != 'development':
            self.skipTest('Registration uses development mode')
        source = self.root / 'external/new app'
        source.mkdir(parents=True)
        original = 'APP_SECRET=keep_me\nDB_USERNAME=root\n'
        (source / '.env').write_text(original)
        self.stack('add', 'registered', str(source), '8.4', 'registered.local', 'registered', ok=False)
        self.assertEqual((source / '.env').read_text(), original)
        self.assertFalse((self.root / 'projects/registered').exists())

    def test_new_project_registration_creates_a_readable_starter(self):
        if self.mode != 'development':
            self.skipTest('Registration uses development mode')
        self.command('make', 'project-add', 'NAME=newapp', 'PHP=8.4', 'DOCUMENT_ROOT=public')
        source = self.root / 'projects/newapp'
        self.assertIn('newapp is running', (source / 'public/index.php').read_text())
        self.assertEqual((source / 'public/index.php').stat().st_mode & 0o777, 0o644)
        self.assertEqual((source / '.env').stat().st_mode & 0o777, 0o644)
        self.assertIn('WORKSPACE_DOMAIN_ALIASES=', (source / '.env.example').read_text())
        self.assertTrue((source / '.docker/Dockerfile').is_file())

    def test_invalid_registration_document_root_never_deletes_existing_environment(self):
        if self.mode != 'development':
            self.skipTest('Registration uses development mode')
        source = self.root / 'external/customer'
        source.mkdir(parents=True)
        env = source / '.env'
        original = b'APPLICATION_SECRET=preserve_this\n'
        env.write_bytes(original)
        self.stack('add', 'customer', str(source), '8.4', 'customer.local', 'customer', 'missing', ok=False)
        self.assertEqual(env.read_bytes(), original)
        self.assertFalse((self.root / 'projects/customer').exists())

    def test_setup_preserves_private_configuration_and_does_not_start_containers(self):
        folder = self.root / 'bin'
        folder.mkdir()
        docker = folder / 'docker'
        real = shutil.which('docker')
        docker.write_text(f'#!/bin/sh\nif [ "$1" = info ]; then exit 0; fi\nexec "{real}" "$@"\n')
        docker.chmod(0o755)
        path = self.root / ('docker/.env.production' if self.mode == 'production' else 'docker/.env')
        original = path.read_bytes()
        self.stack('setup', env={'PATH': str(folder) + os.pathsep + os.environ['PATH']})
        self.assertEqual(path.read_bytes(), original)
        docker.write_text('#!/bin/sh\nif [ "$1" = info ]; then exit 1; fi\nexit 0\n')
        result = self.stack('doctor', ok=False, env={'PATH': str(folder) + os.pathsep + os.environ['PATH']})
        self.assertIn('Cannot reach Docker', result.stderr)

    def test_registration_defaults_to_php85_and_preserves_explicit_versions(self):
        if self.mode != 'development':
            self.skipTest('Registration uses development mode')
        for name, override, expected in (('defaultphp', [], '8.5'), ('explicitphp', ['PHP=8.4'], '8.4')):
            self.command('make', 'project-add', f'NAME={name}', 'DOCUMENT_ROOT=public', *override)
            for filename in ('.env', '.env.example'):
                self.assertIn(f'WORKSPACE_PHP_VERSION={expected}', (self.root / 'projects' / name / filename).read_text())
            self.assertEqual(self.resolved(name)['services']['php']['build']['args']['PHP_VERSION'], expected)
        self.command('make', 'project-add', 'NAME=invalidphp', 'PHP=9.9', ok=False)
        self.assertFalse((self.root / 'projects/invalidphp').exists())

    def test_php85_is_accepted_in_selected_environment(self):
        self.change('alpha', 'WORKSPACE_PHP_VERSION=8.4', 'WORKSPACE_PHP_VERSION=8.5')
        self.sync('alpha')
        self.assertEqual(self.resolved('alpha')['services']['php']['build']['args']['PHP_VERSION'], '8.5')
