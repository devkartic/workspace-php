"""Regression checks that never start containers or modify real project data."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="php-workspace-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for item in (
            "scripts/projects", "scripts/workspace", "Makefile", "docker/docker-compose.yml",
            "docker/.env.example", "docker/nginx/base.conf",
            "projects/dashboard/.env.example", "projects/dashboard/dev/php.ini",
        ):
            target = self.root / item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / item, target)
        (self.root / "docker/php").mkdir()

    def run_command(self, *args, success=True, env=None):
        result = subprocess.run(args, cwd=self.root, env=env, text=True,
                                capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def snapshot(self):
        paths = list((self.root / "docker/nginx/generated").glob("*.conf"))
        paths += [self.root / "docker/generated/projects.compose.yml",
                  self.root / "docker/generated/databases.sql"]
        return {str(p.relative_to(self.root)): p.read_bytes() for p in paths}

    def test_external_source_and_environment_are_preserved(self):
        app = self.root / "external/customer's app"
        app.mkdir(parents=True)
        (app / ".env").write_text("APPLICATION_SECRET=untouched\n")
        result = self.run_command("./scripts/projects", "add", "external",
                                  str(app), "8.3", "external.local", "external")
        self.assertIn("Added external.", result.stdout)
        self.assertTrue((app / ".env").read_text().startswith("APPLICATION_SECRET=untouched\n"))
        self.assertTrue((self.root / "projects/external").is_symlink())
        self.assertFalse((self.root / "docker/projects").exists())
        self.assertIn("external", self.run_command("./scripts/projects", "list").stdout)
        config = self.run_command(
            "docker", "compose", "--env-file", "docker/.env.example",
            "-f", "docker/docker-compose.yml",
            "-f", "docker/generated/projects.compose.yml", "config", "--format", "json")
        services = json.loads(config.stdout)["services"]
        self.assertEqual(Path(services["external"]["volumes"][0]["source"]).resolve(),
                         app.resolve())

    def test_failed_definition_preserves_all_generated_files(self):
        self.run_command("./scripts/projects", "sync")
        before = self.snapshot()
        bad = self.root / "projects/zbad"
        bad.mkdir()
        (bad / ".env").write_text(
            "WORKSPACE_PROJECT_NAME=zbad\n"
            "WORKSPACE_PHP_VERSION=9.9\nWORKSPACE_HOSTNAME=zbad.local\nDB_DATABASE=zbad\n")
        self.run_command("./scripts/projects", "sync", success=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.root / "docker/generated").glob(".sync.*")), [])

    def test_dotenv_application_values_are_not_executed(self):
        template = self.root / "projects/dashboard/.env.example"
        with template.open("a") as stream:
            stream.write('APP_NAME="Application with spaces"\n'
                         'APP_SECRET=$(touch should-not-exist)\n')
        unregistered = self.root / "projects/unregistered"
        unregistered.mkdir()
        (unregistered / ".env.example").write_text("DB_DATABASE=unregistered\n")
        self.run_command("./scripts/projects", "sync")
        self.assertFalse((self.root / "should-not-exist").exists())
        listed = self.run_command("./scripts/projects", "list").stdout
        self.assertIn("dashboard", listed)
        self.assertNotIn("unregistered", listed)

    def test_compose_failure_rolls_back_registration_and_preserves_config(self):
        self.run_command("./scripts/projects", "sync")
        before = self.snapshot()
        compose = self.root / "docker/docker-compose.yml"
        compose.write_text(compose.read_text() + "\nunsupported_property: true\n")
        app = self.root / "external/app"
        app.mkdir(parents=True)
        self.run_command("./scripts/projects", "add", "external", str(app),
                         "8.3", "external.local", "external", success=False)
        self.assertFalse((app / ".env").exists())
        self.assertFalse((app / ".env.example").exists())
        self.assertFalse((self.root / "projects/external").is_symlink())
        self.assertEqual(self.snapshot(), before)

    def test_removing_project_removes_stale_configuration(self):
        self.run_command("./scripts/projects", "sync")
        env = self.root / "projects/dashboard/.env"
        env.write_text(env.read_text().replace("WORKSPACE_PROJECT_NAME=dashboard", ""))
        self.run_command("./scripts/projects", "sync")
        self.assertEqual(list((self.root / "docker/nginx/generated").glob("*.conf")), [])
        self.assertEqual((self.root / "docker/generated/projects.compose.yml").read_text(),
                         "services: {}\n")

    def test_mariadb_shell_uses_container_credentials(self):
        mock_bin = self.root / "bin"
        mock_bin.mkdir()
        docker = mock_bin / "docker"
        docker.write_text(
            '#!/bin/sh\nwhile [ "$1" != exec ]; do shift; done\n'
            'shift\n[ "$1" = mariadb ] || exit 1\nshift\n'
            'export MARIADB_ROOT_PASSWORD="container password with $ and spaces"\n'
            'exec "$@"\n')
        mariadb = mock_bin / "mariadb"
        mariadb.write_text('#!/bin/sh\nprintf "%s" "$MYSQL_PWD" > password-used\n')
        docker.chmod(0o755)
        mariadb.chmod(0o755)
        env = dict(os.environ, PATH=str(mock_bin) + os.pathsep + os.environ["PATH"],
                   MARIADB_ROOT_PASSWORD="incorrect-host-password")
        self.run_command("make", "mariadb", env=env)
        self.assertEqual((self.root / "password-used").read_text(),
                         "container password with $ and spaces")

    def test_dashboard_reports_runtime_without_exposing_configuration(self):
        shutil.copy2(SOURCE / "projects/dashboard/index.php",
                     self.root / "projects/dashboard/index.php")
        result = self.run_command(
            "php", "-r", '$_SERVER["DOCUMENT_ROOT"] = "/var/www/projects/dashboard"; '
            'require "projects/dashboard/index.php";')
        self.assertIn("PHP version:", result.stdout)
        self.assertNotIn("phpinfo()", result.stdout)
        self.assertNotIn("Failed opening", result.stdout + result.stderr)

    def test_creation_requires_only_name_and_version(self):
        self.run_command("make", "project-add", "NAME=my_app", "PHP=8.4")
        project = self.root / "projects/my_app"
        self.assertIn("PHP_VERSION=8.4", (project / ".env").read_text())
        self.assertIn("HOSTNAME=my-app.local", (project / ".env").read_text())
        self.assertIn("DB_DATABASE=my_app", (project / ".env").read_text())
        self.assertEqual(list(self.root.rglob("project.env")), [])
        self.assertTrue((project / ".env.example").exists())
        self.assertIn("WORKSPACE_PROJECT_NAME=my_app", (project / ".env.example").read_text())
        self.assertIn("WORKSPACE_PROJECT_NAME=my_app", (project / ".env").read_text())
        result = self.run_command("php", str(project / "index.php"))
        self.assertIn("my_app is running", result.stdout)
        self.run_command("./scripts/projects", "check", "my_app")
        self.run_command("./scripts/projects", "check", "missing", success=False)

    def test_existing_application_environment_and_code_are_preserved(self):
        project = self.root / "projects/existing"
        project.mkdir()
        (project / ".env").write_text("APPLICATION_SECRET=untouched\n")
        (project / "index.php").write_text("<?php echo 'existing code';\n")
        self.run_command("make", "project-add", "NAME=existing", "PHP=8.3")
        self.assertTrue((project / ".env").read_text().startswith("APPLICATION_SECRET=untouched\n"))
        self.assertEqual((project / "index.php").read_text(), "<?php echo 'existing code';\n")

    def test_ini_mount_is_optional_and_project_specific(self):
        self.run_command("make", "project-add", "NAME=api", "PHP=8.4")
        def services():
            return json.loads(self.run_command(
                "docker", "compose", "--env-file", "docker/.env.example",
                "-f", "docker/docker-compose.yml", "-f",
                "docker/generated/projects.compose.yml", "config", "--format", "json"
            ).stdout)["services"]
        self.assertEqual(len(services()["api"]["volumes"]), 1)
        dev = self.root / "projects/api/dev"
        dev.mkdir(exist_ok=True)
        (dev / "php.ini").write_text("memory_limit = 256M\n")
        self.run_command("./scripts/projects", "sync")
        config = services()
        override = config["api"]["volumes"][1]
        self.assertEqual(Path(override["source"]).resolve(), (dev / "php.ini").resolve())
        self.assertEqual(override["target"], "/usr/local/etc/php/conf.d/zz-project.ini")
        self.assertTrue(override["read_only"])
        self.assertEqual(config["dashboard"]["build"]["args"]["PHP_VERSION"], "7.4")
        self.assertEqual(config["api"]["build"]["args"]["PHP_VERSION"], "8.4")

    def test_public_document_root_maps_nginx_and_php_to_same_file(self):
        self.run_command("make", "project-add", "NAME=web", "PHP=8.4",
                         "DOCUMENT_ROOT=public")
        project = self.root / "projects/web"
        self.assertTrue((project / "public/index.php").is_file())
        self.assertFalse((project / "index.php").exists())
        config = (self.root / "docker/nginx/generated/web.conf").read_text()
        self.assertIn("root /var/www/projects/web/public;", config)
        self.assertIn("SCRIPT_FILENAME /var/www/html/public$fastcgi_script_name;", config)
        self.assertIn("DOCUMENT_ROOT /var/www/html/public;", config)

    def test_project_port_and_hostname_route_to_same_app(self):
        self.run_command("make", "project-add", "NAME=api", "PHP=8.4", "HTTP_PORT=8090")
        config = (self.root / "docker/nginx/generated/api.conf").read_text()
        self.assertIn("listen 80;", config)
        self.assertIn("listen 8090;", config)
        self.assertIn("server_name api.local;", config)
        services = json.loads(self.run_command(
            "docker", "compose", "--env-file", "docker/.env.example",
            "-f", "docker/docker-compose.yml", "-f",
            "docker/generated/projects.compose.yml", "config", "--format", "json"
        ).stdout)["services"]
        port = next(p for p in services["nginx"]["ports"] if p["target"] == 8090)
        self.assertEqual(port["published"], "8090")
        self.assertEqual(port["host_ip"], "127.0.0.1")
        before = self.snapshot()
        for invalid in ("8090", "8082", "80", "65536", "invalid"):
            self.run_command("make", "project-add", "NAME=other", "PHP=8.4",
                             "HTTP_PORT=" + invalid, success=False)
            self.assertEqual(self.snapshot(), before)
        self.run_command("make", "project-add", "NAME=other", "PHP=8.4")
        self.assertIn("WORKSPACE_HTTP_PORT=8084",
                      (self.root / "projects/other/.env").read_text())

    def test_existing_laravel_runtime_volumes_and_source_are_preserved(self):
        app = self.root / "external/laravel"
        for directory in ("public", "storage", "bootstrap/cache", "dev/logs"):
            (app / directory).mkdir(parents=True, exist_ok=True)
        (app / ".env").write_text("APP_SECRET=untouched\n")
        (app / "dev/logs/php-error.log").write_text("existing log\n")
        registration = app / ".env"
        (self.root / "projects/laravel").symlink_to(app, target_is_directory=True)
        registration.write_text(
            "APP_SECRET=untouched\nWORKSPACE_PROJECT_NAME=laravel\n"
            "WORKSPACE_PHP_VERSION=8.4\nWORKSPACE_HOSTNAME=laravel.local\n"
            "DB_DATABASE=laravel\nWORKSPACE_DOCUMENT_ROOT=public\n"
            "WORKSPACE_HTTP_PORT=8090\nWORKSPACE_STORAGE_VOLUME=old_storage\n"
            "WORKSPACE_CACHE_VOLUME=old_bootstrap-cache\n")
        before = {str(p): p.read_bytes() for p in app.rglob("*") if p.is_file()}
        self.run_command("./scripts/projects", "sync")
        self.assertEqual(before, {str(p): p.read_bytes() for p in app.rglob("*") if p.is_file()})
        config = json.loads(self.run_command(
            "docker", "compose", "--env-file", "docker/.env.example", "-f",
            "docker/docker-compose.yml", "-f", "docker/generated/projects.compose.yml",
            "config", "--format", "json").stdout)
        mounts = {v["target"]: v for v in config["services"]["laravel"]["volumes"]}
        self.assertTrue(mounts["/var/www/html"]["read_only"])
        self.assertEqual(mounts["/var/www/html/storage"]["source"], "laravel_storage")
        self.assertTrue(config["volumes"]["laravel_storage"]["external"])
        self.assertEqual(config["volumes"]["laravel_storage"]["name"], "old_storage")
        self.assertEqual(config["volumes"]["laravel_cache"]["name"], "old_bootstrap-cache")
        self.assertIn("alias /var/www/projects/laravel/storage/app/public/;",
                      (self.root / "docker/nginx/generated/laravel.conf").read_text())

    def test_invalid_document_root_and_duplicate_host_preserve_configuration(self):
        self.run_command("./scripts/projects", "sync")
        before = self.snapshot()
        (self.root / "projects/web").mkdir()
        for document_root in ("../outside", "/public", "public;", "missing"):
            self.run_command("make", "project-add", "NAME=web", "PHP=8.4",
                             "DOCUMENT_ROOT=" + document_root, success=False)
            self.assertEqual(self.snapshot(), before)
            template = self.root / "projects/web/.env.example"
            if template.exists():
                self.assertNotIn("WORKSPACE_PROJECT_NAME=", template.read_text())
        self.run_command("make", "project-add", "NAME=web", "PHP=8.4",
                         "HOST=dashboard.local", success=False)
        self.assertEqual(self.snapshot(), before)
        for name, host, document_root in (
            ("web\nbad", "web.local", "."),
            ("web", "web.local\nbad", "."),
            ("web", "web.local", "public\nbad"),
        ):
            self.run_command("./scripts/projects", "add", name, "projects/web",
                             "8.4", host, "web", document_root, success=False)
            self.assertEqual(self.snapshot(), before)

    def test_runtime_env_controls_generation_and_preserves_database_choice(self):
        self.run_command("./scripts/projects", "sync")
        env = self.root / "projects/dashboard/.env"
        env.write_text(env.read_text().replace("WORKSPACE_PHP_VERSION=7.4", "WORKSPACE_PHP_VERSION=8.4")
                       .replace("DB_DATABASE=dashboard", "DB_DATABASE=chosen_db"))
        self.run_command("./scripts/projects", "sync")
        generated = (self.root / "docker/generated/projects.compose.yml").read_text()
        self.assertIn('PHP_VERSION: "8.4"', generated)
        self.assertIn('`chosen_db`', (self.root / "docker/generated/databases.sql").read_text())
        self.assertNotIn("environment:", generated)
        self.assertIn('PHP_VERSION=7.4', (env.parent / ".env.example").read_text())
        app = self.root / "projects/existing_db"
        app.mkdir()
        (app / ".env").write_text("DB_DATABASE=already_selected\nDB_PASSWORD=keep_this\n")
        self.run_command("make", "project-add", "NAME=existing_db", "PHP=8.4")
        self.assertTrue((app / ".env").read_text().startswith(
            "DB_DATABASE=already_selected\nDB_PASSWORD=keep_this\n"))
        self.assertIn('`already_selected`', (self.root / "docker/generated/databases.sql").read_text())

    def test_failed_registration_restores_existing_env_exactly(self):
        self.run_command("./scripts/projects", "sync")
        app = self.root / "external/rollback"
        app.mkdir(parents=True)
        original = "APP_NAME=Existing\nDB_PASSWORD=untouched\n"
        (app / ".env").write_text(original)
        self.run_command("./scripts/projects", "add", "rollback", str(app),
                         "8.4", "dashboard.local", "rollback", success=False)
        self.assertEqual((app / ".env").read_text(), original)
        self.assertFalse((app / ".env.example").exists())
        self.assertFalse((self.root / "projects/rollback").is_symlink())

    def test_setup_preserves_local_configuration_and_checks_daemon(self):
        mock_bin = self.root / "bin"
        mock_bin.mkdir()
        docker = mock_bin / "docker"
        real_docker = shutil.which("docker")
        # Only daemon access is mocked; generation still uses real Compose validation.
        docker.write_text('#!/bin/sh\nif [ "$1" = info ]; then exit "${DOCKER_STATUS:-0}"; fi\n'
                          'exec "' + real_docker + '" "$@"\n')
        docker.chmod(0o755)
        env = dict(os.environ, PATH=str(mock_bin) + os.pathsep + os.environ["PATH"])
        self.run_command("make", "setup", env=env)
        local_env = self.root / "docker/.env"
        local_env.write_text(local_env.read_text().replace("NGINX_HTTP_PORT=80", "NGINX_HTTP_PORT=8090"))
        before = local_env.read_bytes()
        self.run_command("make", "setup", env=env)
        self.assertEqual(local_env.read_bytes(), before)
        env["DOCKER_STATUS"] = "1"
        result = self.run_command("make", "doctor", env=env, success=False)
        self.assertIn("Cannot reach Docker", result.stderr)


if __name__ == "__main__":
    unittest.main()
