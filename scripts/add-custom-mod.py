#!/usr/bin/env python3
"""
Добавляет в модпак мод, которого нет на Modrinth и CurseForge.

    python scripts/add-custom-mod.py путь/к/моду.jar [both|client|server]

Что делает:
  1. копирует jar в custom-mods/ (оттуда его раздаёт Cloudflare);
  2. считает sha256;
  3. пишет wanderlust-create/mods/<имя>.pw.toml со ссылкой на Cloudflare;
  4. обновляет index.toml и pack.toml (packwiz refresh).

Дальше остаётся закоммитить и запушить — лаунчер игроков подтянет мод сам.

Почему не `packwiz url add`: та команда скачивает файл по ссылке, чтобы
посчитать хеш, то есть jar должен УЖЕ лежать в интернете. Получается
двухшаговый порядок: сначала запушить, дождаться деплоя, потом
добавлять в пак. Здесь хеш считается локально, поэтому шаг один.
"""

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
CUSTOM_MODS = REPO / "custom-mods"
PACK = REPO / "wanderlust-create"
MODS_META = PACK / "mods"
BASE_URL = "https://wanderlust-launcher.ruslanyik8.workers.dev/custom-mods/"
PACKWIZ = Path.home() / "tools" / "packwiz.exe"

SIDES = ("both", "client", "server")


def read_mod_meta(jar: Path):
    """Вытаскивает modId, displayName и version из мода, если получится."""
    try:
        with zipfile.ZipFile(jar) as z:
            raw = z.read("META-INF/neoforge.mods.toml").decode("utf-8", "replace")
    except Exception:
        return None, None, None

    def field(name):
        m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', raw, re.MULTILINE)
        return m.group(1) if m else None

    return field("modId"), field("displayName"), field("version")


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 1

    jar = Path(sys.argv[1]).expanduser().resolve()
    side = (sys.argv[2] if len(sys.argv) > 2 else "both").lower()

    if not jar.is_file():
        print(f"нет такого файла: {jar}")
        return 1
    if jar.suffix != ".jar":
        print(f"это не jar: {jar.name}")
        return 1
    if side not in SIDES:
        print(f"side должен быть одним из {', '.join(SIDES)}, а не {side!r}")
        return 1

    mod_id, display_name, version = read_mod_meta(jar)
    if mod_id is None:
        print("предупреждение: не читается META-INF/neoforge.mods.toml — "
              "имя и версию беру из имени файла")

    CUSTOM_MODS.mkdir(exist_ok=True)
    target = CUSTOM_MODS / jar.name
    if target.resolve() != jar:
        shutil.copy2(jar, target)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    url = BASE_URL + quote(jar.name)

    slug = mod_id or re.sub(r"[^a-zA-Z0-9_-]+", "-", jar.stem).strip("-").lower()
    meta_path = MODS_META / f"{slug}.pw.toml"

    name = display_name or slug
    ver = version or "custom"

    meta_path.write_text(
        f"filename = '{jar.name}'\n"
        f"name = '{name}'\n"
        f"side = '{side}'\n"
        f"x-prismlauncher-loaders = [ 'neoforge' ]\n"
        f"x-prismlauncher-mc-versions = [ '1.21.1' ]\n"
        f"x-prismlauncher-release-type = 'release'\n"
        f"x-prismlauncher-version-number = '{ver}'\n"
        f"\n"
        f"# Раздаётся из custom-mods/ этого репозитория через GitHub Pages.\n"
        f"# Блока [update.*] здесь нет намеренно: packwiz update не должен\n"
        f"# пытаться искать этот мод на Modrinth и перетирать ссылку.\n"
        f"\n"
        f"[download]\n"
        f"hash = '{digest}'\n"
        f"hash-format = 'sha256'\n"
        f"mode = 'url'\n"
        f"url = '{url}'\n",
        encoding="utf-8",
    )

    subprocess.run([str(PACKWIZ), "refresh"], cwd=PACK, check=True)

    print()
    print(f"  мод      {name} {ver}")
    print(f"  файл     custom-mods/{jar.name}")
    print(f"  запись   wanderlust-create/mods/{meta_path.name}")
    print(f"  сторона  {side}" + ("  (клиентам не раздаётся)" if side == "server" else ""))
    print(f"  sha256   {digest}")
    print()
    print("осталось запушить:")
    print(f"  git add custom-mods/{jar.name} wanderlust-create")
    print(f'  git commit -m "Добавлен мод {name}"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
