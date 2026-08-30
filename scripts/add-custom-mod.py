#!/usr/bin/env python3
"""
Добавляет в модпак мод, которого нет на Modrinth и CurseForge.

    python scripts/add-custom-mod.py путь/к/моду.jar [both|client|server] [--pack ИМЯ]

Пак по умолчанию — wanderlust-create. Для другой сборки:
    python scripts/add-custom-mod.py mod.jar both --pack stray-souls

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
DEFAULT_PACK = "wanderlust-create"
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

    args = [a for a in sys.argv[1:] if a != "--pack"]
    pack_name = DEFAULT_PACK
    if "--pack" in sys.argv:
        i = sys.argv.index("--pack")
        if i + 1 < len(sys.argv):
            pack_name = sys.argv[i + 1]
            args = [a for a in args if a != pack_name]

    pack = REPO / pack_name
    mods_meta = pack / "mods"
    if not (pack / "pack.toml").is_file():
        print(f"нет пака: {pack_name} (ожидался {pack / 'pack.toml'})")
        return 1

    # Версию Minecraft берём из самого пака, а не хардкодим: сборки разные.
    mc_version = "1.21.1"
    m = re.search(r'minecraft\s*=\s*"([^"]+)"', (pack / "pack.toml").read_text(encoding="utf-8"))
    if m:
        mc_version = m.group(1)

    jar = Path(args[0]).expanduser().resolve()
    side = (args[1] if len(args) > 1 else "both").lower()

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
    # В свежесозданном паке папки mods/ ещё нет — packwiz init её не делает.
    mods_meta.mkdir(parents=True, exist_ok=True)
    meta_path = mods_meta / f"{slug}.pw.toml"

    name = display_name or slug
    ver = version or "custom"

    # Имя мода — человеческий текст, в нём бывают апострофы («NukaTeam's Gun
    # Lib»). В одинарных кавычках TOML такой апостроф закрывает строку и ломает
    # файл, поэтому имя пишем как basic string в двойных кавычках с экранированием.
    def toml_basic(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    meta_path.write_text(
        f"filename = '{jar.name}'\n"
        f"name = {toml_basic(name)}\n"
        f"side = '{side}'\n"
        f"x-prismlauncher-loaders = [ 'neoforge' ]\n"
        f"x-prismlauncher-mc-versions = [ '{mc_version}' ]\n"
        f"x-prismlauncher-release-type = 'release'\n"
        f"x-prismlauncher-version-number = '{ver}'\n"
        f"\n"
        f"# Раздаётся из custom-mods/ этого репозитория через Cloudflare.\n"
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

    subprocess.run([str(PACKWIZ), "refresh"], cwd=pack, check=True)

    print()
    print(f"  мод      {name} {ver}")
    print(f"  файл     custom-mods/{jar.name}")
    print(f"  запись   {pack_name}/mods/{meta_path.name}")
    print(f"  сторона  {side}" + ("  (клиентам не раздаётся)" if side == "server" else ""))
    print(f"  sha256   {digest}")
    print()
    print("осталось запушить:")
    print(f"  git add custom-mods/{jar.name} {pack_name}")
    print(f'  git commit -m "Добавлен мод {name}"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
