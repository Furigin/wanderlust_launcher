#!/usr/bin/env python3
"""
Выпускает новую версию лаунчера: сборка, публикация, обновление у игроков.

    python scripts/release-launcher.py            # 0.2.4 -> 0.2.5
    python scripts/release-launcher.py 0.3.0      # конкретная версия

Что делает по шагам:
  1. поднимает версию в tauri.conf.json и Cargo.toml лаунчера;
  2. гоняет тесты (падение тестов останавливает релиз);
  3. собирает exe;
  4. кладёт его в download/ этого репозитория;
  5. прописывает версию и sha256 в manifest.json;
  6. коммитит и пушит.

Дальше Cloudflare разложит файлы за минуту, и все лаунчеры обновятся сами
при следующем запуске — раздавать ничего вручную не нужно.

Проверка перед пушем обязательна: манифест говорит игрокам «есть версия N»
и даёт хеш. Если хеш не сойдётся с файлом, лаунчер откажется обновляться,
и починить это можно будет только новым релизом.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = Path(r"C:\Users\rusla\Desktop\laincher")
TAURI_CONF = LAUNCHER / "src-tauri" / "tauri.conf.json"
CARGO_TOML = LAUNCHER / "src-tauri" / "Cargo.toml"
BUILT_EXE = LAUNCHER / "src-tauri" / "target" / "release" / "app.exe"
DEST_EXE = REPO / "download" / "Wanderlust.exe"
MANIFEST = REPO / "manifest.json"


def run(cmd, cwd, what):
    print(f"\n>>> {what}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"\nОстановился: {what} завершилось с кодом {result.returncode}")
        sys.exit(1)


def bump(version: str) -> str:
    """0.2.4 -> 0.2.5. Ломается намеренно на нечисловых версиях."""
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def main():
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    current = conf["version"]
    new_version = sys.argv[1] if len(sys.argv) > 1 else bump(current)

    print(f"Версия: {current} -> {new_version}")

    # 1. версия в двух местах: Tauri берёт её для окна, Cargo — для
    #    CURRENT_VERSION в update.rs, по которому лаунчер решает, обновляться ли.
    conf["version"] = new_version
    TAURI_CONF.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cargo = CARGO_TOML.read_text(encoding="utf-8")
    cargo = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{new_version}"',
                   cargo, count=1, flags=re.MULTILINE)
    CARGO_TOML.write_text(cargo, encoding="utf-8")

    # 2-3. тесты и сборка
    run(["cargo", "test", "--lib"], LAUNCHER / "src-tauri", "тесты")
    run(["cargo", "tauri", "build"], LAUNCHER, "сборка exe")

    if not BUILT_EXE.is_file():
        print(f"\nНе нашёл собранный exe: {BUILT_EXE}")
        sys.exit(1)

    # 4. кладём в репозиторий
    DEST_EXE.parent.mkdir(exist_ok=True)
    shutil.copy2(BUILT_EXE, DEST_EXE)

    # 5. манифест: версия и хеш. Хеш считаем от того файла, который реально
    #    уедет игрокам, а не от исходного — так исключаем расхождение.
    digest = hashlib.sha256(DEST_EXE.read_bytes()).hexdigest()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["launcher"]["version"] = new_version
    manifest["launcher"]["sha256"] = digest
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    size_mb = DEST_EXE.stat().st_size / 1048576
    print(f"\nexe: {size_mb:.1f} МБ")
    print(f"sha256: {digest}")

    # 6. публикация
    run(["git", "add", "-A"], REPO, "git add")
    run(["git", "commit", "-m", f"Лаунчер {new_version}"], REPO, "git commit")
    run(["git", "push", "origin", "main"], REPO, "git push")

    print(f"\nГотово. Версия {new_version} опубликована.")
    print("Cloudflare разложит файлы за минуту, дальше лаунчеры обновятся сами.")
    print("\nНе забудь закоммитить исходники лаунчера отдельно:")
    print(f"  cd {LAUNCHER}")
    print(f'  git add -A && git commit -m "Версия {new_version}" && git push')
    return 0


if __name__ == "__main__":
    sys.exit(main())
