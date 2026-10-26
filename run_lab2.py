"""Uruchom ten plik w PyCharmie, aby załadować powiat i siatki lokalnie."""
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    compose = ["docker", "compose", "-f", str(root / "docker-compose-local.yml")]
    subprocess.run(compose + ["up", "-d", "--build", "--wait"], cwd=root, check=True)
    subprocess.run(compose + ["exec", "-T", "python", "python", "/app/load.py"], cwd=root, check=True)
    print("Gotowe: powiat i siatki są w lokalnej bazie na porcie 5444.")


if __name__ == "__main__":
    main()
