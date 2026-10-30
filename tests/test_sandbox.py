from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest
from docker.errors import DockerException, ImageNotFound

from devpilot.sandbox import (
    DEFAULT_BASE_IMAGE,
    DEPS_DIR,
    PYTEST_VERSION,
    WORKDIR,
    DockerSandbox,
    SandboxError,
    dependency_sources,
    sandbox_image_tag,
)


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    container = MagicMock()
    container.exec_run.return_value = (0, b"hello\n")
    client.containers.run.return_value = container
    return client


def test_image_tag_is_docker_safe_and_tracks_pins():
    assert sandbox_image_tag("python:3.12-slim") == (
        f"devpilot-sandbox:python-3.12-slim-pytest-{PYTEST_VERSION}"
    )
    pinned = sandbox_image_tag(DEFAULT_BASE_IMAGE)
    assert len(pinned.split(":", 1)[1]) <= 128
    assert pinned != sandbox_image_tag("python:3.12-slim@sha256:" + "0" * 64)


def test_default_base_image_is_pinned_by_digest():
    assert DEFAULT_BASE_IMAGE.startswith("python:3.12-slim@sha256:")


def test_dependency_sources(tmp_path):
    assert dependency_sources(tmp_path) == ([], [])

    (tmp_path / "requirements.txt").write_text("fastapi==0.115.0\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["sqlalchemy>=2"]\n'
        '[project.optional-dependencies]\ntest = ["pytest-asyncio"]\ndocs = ["sphinx"]\n'
        '[dependency-groups]\ndev = ["redis", {include-group = "test"}, "--index-url=x"]\n'
    )

    sources, pip_args = dependency_sources(tmp_path)

    assert sources == ["requirements.txt", "pyproject.toml"]
    assert pip_args == ["-r", "requirements.txt", "sqlalchemy>=2", "pytest-asyncio", "redis"]


def test_broken_pyproject_is_ignored(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not = [valid")

    assert dependency_sources(tmp_path) == ([], [])


def test_dependencies_install_in_a_separate_networked_container(tmp_path, fake_client):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    setup = MagicMock()
    setup.wait.return_value = {"StatusCode": 0}
    run_container = fake_client.containers.run.return_value
    fake_client.containers.run.side_effect = [setup, run_container]

    sandbox = DockerSandbox(tmp_path, install_dependencies=True, client=fake_client)
    sandbox.start()

    setup_kwargs, run_kwargs = (c.kwargs for c in fake_client.containers.run.call_args_list)
    assert setup_kwargs["network_mode"] == "bridge"
    assert setup_kwargs["volumes"][str(tmp_path.resolve())]["mode"] == "ro"
    assert setup_kwargs["command"][-2:] == ["-r", "requirements.txt"]
    assert setup_kwargs["read_only"] is True
    setup.remove.assert_called_once_with(force=True)

    assert run_kwargs["network_mode"] == "none"
    deps = [v for v in run_kwargs["volumes"].values() if v["bind"] == DEPS_DIR]
    assert deps == [{"bind": DEPS_DIR, "mode": "ro"}]
    assert run_kwargs["environment"]["PATH"].startswith(f"{DEPS_DIR}/venv/bin:")
    assert "requirements.txt were installed" in sandbox.setup_report

    deps_dir = sandbox._deps_dir
    sandbox.close()
    assert not deps_dir.exists()


def test_failed_install_is_reported_and_run_continues_offline(tmp_path, fake_client):
    (tmp_path / "requirements.txt").write_text("does-not-exist\n")
    setup = MagicMock()
    setup.wait.return_value = {"StatusCode": 1}
    setup.logs.return_value = b"ERROR: No matching distribution found"
    run_container = fake_client.containers.run.return_value
    fake_client.containers.run.side_effect = [setup, run_container]

    sandbox = DockerSandbox(tmp_path, install_dependencies=True, client=fake_client)
    sandbox.start()

    run_kwargs = fake_client.containers.run.call_args.kwargs
    assert run_kwargs["network_mode"] == "none"
    assert "PATH" not in run_kwargs["environment"]
    assert "failed with exit code 1" in sandbox.setup_report
    assert "No matching distribution" in sandbox.setup_report


def test_no_install_without_dependency_files(tmp_path, fake_client):
    sandbox = DockerSandbox(tmp_path, install_dependencies=True, client=fake_client)
    sandbox.start()

    assert fake_client.containers.run.call_count == 1
    assert sandbox.setup_report is None


def test_start_mounts_only_the_workspace_with_hardening(tmp_path, fake_client):
    sandbox = DockerSandbox(tmp_path, client=fake_client)
    sandbox.start()

    kwargs = fake_client.containers.run.call_args.kwargs
    assert kwargs["volumes"] == {str(tmp_path.resolve()): {"bind": WORKDIR, "mode": "rw"}}
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"]
    assert kwargs["pids_limit"] > 0


def test_network_can_be_enabled(tmp_path, fake_client):
    DockerSandbox(tmp_path, network=True, client=fake_client).start()

    assert fake_client.containers.run.call_args.kwargs["network_mode"] == "bridge"


def test_builds_image_when_missing(tmp_path, fake_client):
    fake_client.images.get.side_effect = ImageNotFound("missing")

    DockerSandbox(tmp_path, client=fake_client).start()

    build_kwargs = fake_client.images.build.call_args.kwargs
    assert build_kwargs["tag"] == sandbox_image_tag(DEFAULT_BASE_IMAGE)
    dockerfile = build_kwargs["fileobj"].getvalue().decode()
    assert f"FROM {DEFAULT_BASE_IMAGE}" in dockerfile
    assert f"pytest=={PYTEST_VERSION}" in dockerfile


def test_run_wraps_command_with_timeout(tmp_path, fake_client):
    sandbox = DockerSandbox(tmp_path, timeout=30, client=fake_client)
    sandbox.start()

    result = sandbox.run("echo hello")

    argv = fake_client.containers.run.return_value.exec_run.call_args.args[0]
    assert argv[:4] == ["timeout", "-k", "5", "30"]
    assert argv[-1] == "echo hello"
    assert result.exit_code == 0
    assert result.output == "hello\n"
    assert not result.timed_out


def test_run_reports_timeout(tmp_path, fake_client):
    fake_client.containers.run.return_value.exec_run.return_value = (124, b"")
    sandbox = DockerSandbox(tmp_path, client=fake_client)
    sandbox.start()

    result = sandbox.run("sleep 999", timeout=2)

    assert result.timed_out
    assert "timed out after 2s" in result.output


def test_run_before_start_raises(tmp_path, fake_client):
    with pytest.raises(SandboxError):
        DockerSandbox(tmp_path, client=fake_client).run("ls")


def test_context_manager_removes_container(tmp_path, fake_client):
    with DockerSandbox(tmp_path, client=fake_client):
        pass

    fake_client.containers.run.return_value.remove.assert_called_once_with(force=True)


def test_unreachable_daemon_raises_sandbox_error(tmp_path, monkeypatch):
    def broken_from_env():
        raise DockerException("no daemon")

    monkeypatch.setattr(docker, "from_env", broken_from_env)

    with pytest.raises(SandboxError, match="Docker daemon"):
        DockerSandbox(tmp_path).start()


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
    except DockerException:
        return False
    return True


@pytest.mark.docker
@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")
def test_real_container_isolation(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("from host")

    with DockerSandbox(tmp_path, timeout=20) as sandbox:
        assert sandbox.run("cat hello.txt").output == "from host"
        assert sandbox.run("echo made > out.txt").exit_code == 0
        assert sandbox.run("touch /etc/evil").exit_code != 0
        connect = "python -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), 3)'"
        assert sandbox.run(connect).exit_code != 0
        assert sandbox.run("sleep 30", timeout=2).timed_out

    assert (tmp_path / "out.txt").read_text().strip() == "made"
