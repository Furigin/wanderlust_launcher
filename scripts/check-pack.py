#!/usr/bin/env python3
"""
Проверяет, что пак собран верно и его реально можно установить.

    python scripts/check-pack.py                          # локально
    python scripts/check-pack.py --online                 # ещё и то, что раздаётся
    python scripts/check-pack.py --pack stray-souls       # другая сборка

Что сверяется:
  1. index.toml против файлов на диске — хеш каждой записи;
  2. pack.toml против index.toml — хеш индекса;
  3. кастом-моды: файл из custom-mods/ существует, и его sha256 совпадает
     с тем, что записан в .pw.toml;
  4. с --online: то же самое на живой раздаче, плюс каждый файл из индекса
     проверяется на доступность — установщик роняет установку на первом 404.

Зачем: packwiz не пересчитывает индекс сам. Стоит поправить .pw.toml мимо
add-custom-mod.py и забыть `packwiz refresh` — и у игроков установка падает
с «Invalid mod file hash», хотя локально всё выглядит нормально. Один раз
так и произошло при переезде на Cloudflare.

Возвращает код 1, если что-то не сходится, — можно звать перед пушем.
"""

import hashlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOST = "https://wanderlust-launcher.ruslanyik8.workers.dev"

# Какой пак проверяем: по умолчанию боевой, иначе --pack ИМЯ.
PACK_NAME = "wanderlust-create"
if "--pack" in sys.argv:
    _i = sys.argv.index("--pack")
    if _i + 1 < len(sys.argv):
        PACK_NAME = sys.argv[_i + 1]
PACK = REPO / PACK_NAME

problems: list[str] = []


def digest_bytes(data: bytes, algo: str = "sha256") -> str:
    return hashlib.new(algo, data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return digest_bytes(data, "sha256")


def fetch(url: str) -> bytes | None:
    # User-Agent обязателен: Cloudflare отвечает 403 на запросы без него,
    # и проверка ложно ругалась бы на живую раздачу.
    req = urllib.request.Request(url, headers={"User-Agent": "wanderlust-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except Exception as e:
        problems.append(f"не открывается {url}: {e}")
        return None


def check_index_against_disk() -> None:
    idx_path = PACK / "index.toml"
    idx = idx_path.read_text(encoding="utf-8")
    entries = re.findall(r'file = "([^"]+)"\nhash = "([0-9a-f]+)"', idx)
    print(f"пак {PACK_NAME} — index.toml: {len(entries)} записей")

    for rel, expected in entries:
        f = PACK / rel
        if not f.is_file():
            problems.append(f"в индексе есть {rel}, а файла нет")
            continue
        actual = sha256_bytes(f.read_bytes())
        if actual != expected:
            problems.append(
                f"{rel}: хеш в индексе устарел "
                f"(файл {actual[:12]}…, индекс {expected[:12]}…) — нужен packwiz refresh"
            )


def check_pack_against_index() -> None:
    pack = (PACK / "pack.toml").read_text(encoding="utf-8")
    m = re.search(r'\[index\][^\[]*?hash = "([0-9a-f]+)"', pack, re.S)
    if not m:
        problems.append("в pack.toml не нашёлся хеш индекса")
        return
    actual = sha256_bytes((PACK / "index.toml").read_bytes())
    if actual != m.group(1):
        problems.append(
            f"pack.toml ссылается на другой индекс "
            f"(index.toml {actual[:12]}…, pack.toml {m.group(1)[:12]}…) — нужен packwiz refresh"
        )


def custom_mod_entries() -> list[tuple[str, str, str, str]]:
    """(имя метафайла, ссылка, ожидаемый хеш, алгоритм) — только наши моды.

    Алгоритм у разных записей разный: add-custom-mod.py пишет sha256, а
    часть модов заведена руками с sha512. Проверять надо тем же, чем считали.
    """
    out = []
    for meta in sorted((PACK / "mods").glob("*.pw.toml")):
        text = meta.read_text(encoding="utf-8")
        if "custom-mods/" not in text:
            continue  # мод с Modrinth, за его хеши отвечает Modrinth
        url = re.search(r"""url = ['"]([^'"]+)['"]""", text)
        digest = re.search(r"""hash = ['"]([0-9a-f]{32,128})['"]""", text)
        algo = re.search(r"""hash-format = ['"]([a-z0-9]+)['"]""", text)
        if not url or not digest:
            problems.append(f"{meta.name}: не разобрать url/hash")
            continue
        out.append((meta.name, url.group(1), digest.group(1),
                    algo.group(1) if algo else "sha256"))
    return out


def check_custom_mods_local() -> None:
    entries = custom_mod_entries()
    print(f"кастом-моды: {len(entries)}")
    for name, url, expected, algo in entries:
        filename = urllib.parse.unquote(url.rsplit("/", 1)[1])
        jar = REPO / "custom-mods" / filename
        if not jar.is_file():
            problems.append(f"{name}: нет файла custom-mods/{filename}")
            continue
        actual = digest_bytes(jar.read_bytes(), algo)
        if actual != expected:
            problems.append(
                f"{name}: хеш в .pw.toml не совпадает с jar — перезапусти add-custom-mod.py"
            )


def check_online() -> None:
    print("\nпроверяю раздачу…")
    pack_remote = fetch(f"{HOST}/{PACK_NAME}/pack.toml")
    if pack_remote is None:
        return
    if pack_remote != (PACK / "pack.toml").read_bytes():
        problems.append("на раздаче лежит другой pack.toml — деплой ещё идёт или не прошёл")

    idx_remote = fetch(f"{HOST}/{PACK_NAME}/index.toml")
    if idx_remote is not None and idx_remote != (PACK / "index.toml").read_bytes():
        problems.append("на раздаче лежит другой index.toml — деплой ещё идёт или не прошёл")

    for name, url, expected, algo in custom_mod_entries():
        data = fetch(url)
        if data is None:
            continue
        if digest_bytes(data, algo) != expected:
            problems.append(f"{name}: файл на раздаче не совпадает с хешем в .pw.toml")

    check_every_indexed_file_is_reachable()


def check_every_indexed_file_is_reachable() -> None:
    """Каждый файл из index.toml должен отдаваться раздачей.

    Установщик обрывается на первом же 404 — не пропускает файл, а роняет
    установку целиком, и игрок не может зайти. Ровно так .assetsignore
    выкинул из раздачи config/crash_assistant/scripts/* и README внутри
    конфигов: локально всё было на месте, а у игроков установка падала.

    Ходим HEAD-запросами и параллельно — иначе 492 файла проверялись бы минуты.
    """
    idx = (PACK / "index.toml").read_text(encoding="utf-8")
    files = re.findall(r'file = "([^"]+)"', idx)
    print(f"проверяю доступность {len(files)} файлов индекса…")

    def head(rel: str) -> tuple[str, int]:
        url = f"{HOST}/{PACK_NAME}/" + urllib.parse.quote(rel)
        # Один повтор: при большом числе параллельных запросов Cloudflare
        # иногда рвёт соединение (код 0), и без повтора проверка ложно
        # ругалась бы на живые файлы.
        for attempt in range(2):
            req = urllib.request.Request(
                url, method="HEAD", headers={"User-Agent": "wanderlust-check/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return rel, r.status
            except urllib.error.HTTPError as e:
                return rel, e.code  # реальный HTTP-код (404 и т.п.) — не транзиент
            except Exception:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return rel, 0
        return rel, 0

    # 8 потоков вместо 16: Cloudflare спокойнее, ложных обрывов почти нет.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for rel, code in pool.map(head, files):
            if code != 200:
                problems.append(
                    f"не отдаётся: {rel} (код {code}) — установка у игроков оборвётся здесь"
                )


def check_metafiles_parse() -> None:
    """Каждый .pw.toml должен быть валидным TOML.

    Апостроф в имени мода («NukaTeam's Gun Lib») внутри одинарных кавычек
    закрывает строку и ломает файл. packwiz refresh такой файл молча
    пропускает, а установщик у игроков падает при разборе индекса.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        return
    metas = sorted((PACK / "mods").glob("*.pw.toml"))
    for meta in metas:
        try:
            tomllib.load(meta.open("rb"))
        except Exception as e:
            problems.append(f"{meta.name}: битый TOML ({e})")


def main() -> int:
    check_index_against_disk()
    check_pack_against_index()
    check_metafiles_parse()
    check_custom_mods_local()
    if "--online" in sys.argv:
        check_online()

    print()
    if problems:
        print(f"НАЙДЕНО ПРОБЛЕМ: {len(problems)}")
        for p in problems:
            print("  •", p)
        return 1
    print("Пак в порядке: индекс, pack.toml и кастом-моды сходятся.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
