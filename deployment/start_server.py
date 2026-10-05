"""Start an authenticated lifecycle server using the application's configuration rules."""
import json
import os
from pathlib import Path

from deployment.sandbox_auth import sandbox_api_key


def server_config(environ):
    key = sandbox_api_key(environ)
    source = environ.get("OPEN_SANDBOX_CONFIG_FILE")
    if source:
        # Keep the entire selected server config, including existing egress policy/proxies.
        return Path(source).expanduser().read_text()
    return (
        '[server]\nhost = "0.0.0.0"\nport = 8080\napi_key = ' + json.dumps(key) + '\n'
        '[runtime]\ntype = "docker"\nexecd_image = ' + json.dumps(environ["OPEN_SANDBOX_EXECD_IMAGE"]) + '\n'
        '[docker]\nnetwork_mode = "bridge"\nhost_ip = "host.docker.internal"\n'
        '[proxy]\nresolve_internal = false\n'
    )


def main():
    text = server_config(os.environ)
    config = Path("/tmp/opensandbox.toml")
    # Create with restrictive permissions before writing any credential.
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)
    os.execvp("opensandbox-server", ["opensandbox-server", "--config", str(config)])


if __name__ == "__main__":
    main()
