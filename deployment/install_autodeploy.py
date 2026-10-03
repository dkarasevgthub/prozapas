"""One-time root setup of the restricted GitHub Actions SSH command."""
import os
from pathlib import Path
import subprocess


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run this installer as root on the deployment server")
    source = Path(__file__).resolve().parent
    if not Path("/opt/prozapas/deployment/.env").is_file():
        raise SystemExit("Existing ProZapas deployment is required")
    for filename, destination in (
        ("deploy.py", "/usr/local/sbin/prozapas-deploy"),
        ("deploy_ssh.py", "/usr/local/sbin/prozapas-deploy-ssh"),
    ):
        target = Path(destination)
        target.write_text((source / filename).read_text().replace("\r\n", "\n"))
        target.chmod(0o700)
    ssh = Path("/root/.ssh")
    ssh.mkdir(mode=0o700, exist_ok=True)
    key = ssh / "prozapas_actions"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C",
                        "prozapas-github-actions", "-f", str(key)], check=True,
                       stdout=subprocess.DEVNULL)
    public_key = key.with_suffix(".pub").read_text().strip()
    authorized = ssh / "authorized_keys"
    current = authorized.read_text() if authorized.exists() else ""
    if public_key not in current:
        entry = 'restrict,command="/usr/local/sbin/prozapas-deploy-ssh" ' + public_key
        temporary = ssh / "authorized_keys.prozapas"
        temporary.write_text(current.rstrip() + "\n" + entry + "\n")
        temporary.chmod(0o600)
        temporary.replace(authorized)
    host_key = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()
    (ssh / "prozapas_actions_known_hosts").write_text(
        "185.196.117.2 " + " ".join(host_key[:2]) + "\n")
    marker_key = ssh / "prozapas_release_marker"
    if not marker_key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C",
                        "prozapas-release-marker", "-f", str(marker_key)], check=True,
                       stdout=subprocess.DEVNULL)
    print("Register /root/.ssh/prozapas_release_marker.pub as the repository's write deploy key")
    print("Pin github.com host keys from the official GitHub metadata API before publishing")
    print("Installed restricted deployment command and dedicated Actions key")


if __name__ == "__main__":
    os.umask(0o077)
    main()
