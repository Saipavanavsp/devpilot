from pathlib import Path
from unittest.mock import MagicMock

import docker
import pytest
from docker.errors import DockerException, ImageNotFound

from devpilot.sandbox import WORKDIR, DockerSandbox, SandboxError, sandbox_image_tag


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    container = MagicMock()
    container.exec_run.return_value = (0, b"hello\n")
    client.containers.run.return_value = container
    return client


def test_image_tag_is_docker_safe():
    assert sandbox_image_tag("python:3.12-slim") == "devpilot-sandbox:python-3.12-slim"


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
    assert build_kwargs["tag"] == "devpilot-sandbox:python-3.12-slim"
    assert b"FROM python:3.12-slim" in build_kwargs["fileobj"].getvalue()


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
