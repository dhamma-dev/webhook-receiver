import os
import subprocess
import sys


def main() -> int:
    env = os.environ.copy()
    env.setdefault("OAUTH_PORT", "5001")
    env.setdefault("API_PORT", "5002")
    # Single shared secret for both
    env.setdefault("JWT_SECRET", "dev-jwt-secret-change-in-production")
    env.setdefault("API_AUDIENCE", "protected-api")
    env.setdefault("OAUTH_ISSUER", f"http://localhost:{env['OAUTH_PORT']}")

    procs: list[subprocess.Popen[bytes]] = []
    try:
        procs.append(subprocess.Popen([sys.executable, "oauth_server.py"], env=env))
        procs.append(subprocess.Popen([sys.executable, "protected_api.py"], env=env))
        # Wait until one exits
        return procs[0].wait()
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

