"""Static acceptance checks for the stranger-facing standalone compose."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)


def test_readme_quickstart_is_the_two_command_loopback_path():
    match = re.search(
        r"```bash\n"
        r"(docker compose up -d --build)\n"
        r"(curl -fsS http://127\.0\.0\.1:8765/ready)\n"
        r"```",
        README,
    )
    assert match, "the public quickstart must remain exactly two commands"
    assert ("s" + "sh ") not in README


def test_standalone_compose_keeps_the_public_boundary_and_seed_off():
    services = COMPOSE["services"]
    app = services["knowledge-ops"]
    auth = services["knowledge-ops-auth"]
    environment = app["environment"]

    assert app["ports"] == ["127.0.0.1:8765:8765"]
    assert "ports" not in auth
    assert environment["KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS"] == "0"
    assert environment["KNOWLEDGE_OPS_API_REQUIRE_AUTH"].endswith(":-1}")
    assert "knowledge-ops-auth" in app["depends_on"]
    assert app["depends_on"]["knowledge-ops-auth"]["condition"] == "service_healthy"


def test_bootstrap_auth_volume_is_not_mounted_into_api_or_worker():
    services = COMPOSE["services"]
    auth_mounts = [str(mount) for mount in services["knowledge-ops-auth"]["volumes"]]

    assert "knowledge_ops_bootstrap_auth:/data" in auth_mounts
    assert "knowledge_ops_data:/data" not in auth_mounts
    assert "knowledge_ops_bootstrap_auth" in COMPOSE["volumes"]

    for service_name in ("knowledge-ops", "knowledge-ops-worker"):
        mounts = [str(mount) for mount in services[service_name]["volumes"]]
        assert all("knowledge_ops_bootstrap_auth" not in mount for mount in mounts)
        assert all("/data/bootstrap-auth" not in mount for mount in mounts)


def test_bootstrap_issuer_generates_a_persisted_admin_credential_without_a_secret_literal():
    command = "\n".join(COMPOSE["services"]["knowledge-ops-auth"]["command"])
    assert "bootstrap-admin.token" in command
    assert "bootstrap-admin.key" in command
    assert "ThreadingHTTPServer" in command
    assert "knowledge-admin" in command
    assert "90 * 24 * 60 * 60" in command
    assert not re.search(
        r"password\s*[:=]\s*['\"][^'\"]+['\"]", COMPOSE_TEXT, re.IGNORECASE
    )


def test_public_selfhost_files_do_not_reference_private_deployment_names():
    private_host_fragments = (
        "auth" + "." + "unicorncommander" + "." + "ai",
        "magic" + "unicorn" + "." + "dev",
        "unicorncommander" + "." + "ai",
    )
    for fragment in private_host_fragments:
        assert fragment not in README
        assert fragment not in COMPOSE_TEXT
