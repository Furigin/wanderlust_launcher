#!/usr/bin/env python3
"""
Обновление модов сборки Stray Souls одной командой.

Двойной клик по «Моды Stray Souls.bat» на рабочем столе — или:

    python scripts/mods.py                      — меню
    python scripts/mods.py add путь.jar [...]   — добавить или обновить
    python scripts/mods.py add gazehead         — найти по названию
    python scripts/mods.py remove worldedit     — убрать
    python scripts/mods.py list                 — что сейчас в сборке
    ... --check                                 — прогнать всё, но ничего не
                                                  публиковать и откатить

Что делает сам:
  * по modId внутри jar понимает, новый это мод или обновление уже
    стоящего, и убирает старую версию. Иначе после обновления с
    voicechat-2.6.21 на 2.6.22 в папке лежали бы оба файла, а два jar с
    одним modId роняют игру при запуске;
  * отказывается брать Fabric-моды и моды не под ту версию Minecraft;
  * предупреждает, если моду не хватает зависимости;
  * пересобирает пак (build-private-pack.py), проверяет его и публикует.
    Если сборка упала — всё откатывается, в интернет ничего не уходит.

Чего не делает: не заливает мод на сервер билдеров — пароля от хостинга
у скрипта нет и быть не должно. Об этом он напоминает в конце.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
BASE = Path(r"C:\Users\rusla\Desktop\stray-souls-mods")          # = папка mods на сервере
OPTIONAL = Path(r"C:\Users\rusla\Desktop\Новая папка (9)\моды")  # выборочные
MC_VERSION = "26.2"

# Где искать мод, если указано только название: (папка, обходить ли вглубь).
# «Загрузки» — только верхний уровень: там лежат распакованные программы на
# десятки гигабайт, и рекурсивный обход растягивался бы на минуты.
SEARCH_DIRS = [
    (Path(r"C:\Users\rusla\Downloads"), False),
    (Path(r"C:\Users\rusla\Desktop\финальные моды"), True),
    (Path(r"C:\Users\rusla\Desktop"), False),
]

# Пути в репозитории, которые трогает сборка. Коммитим и откатываем только
# их: чужие незакоммиченные правки в репозитории не должны уехать вместе с
# модами или пропасть при откате.
TOUCHED = ["stray-souls", "stray-souls-files", "p"]

IGNORED_DEPS = {"minecraft", "neoforge", "forge", "java", "fabricloader"}


# ---------------------------------------------------------------- jar -----

@dataclass
class ModInfo:
    path: Path
    mod_id: str | None
    name: str
    version: str
    loader: str            # neoforge / fabric / unknown
    mc_range: str | None   # versionRange зависимости minecraft
    requires: list[str]
    provides: list[str]    # свои modId + вшитые


def _mods_toml(z: zipfile.ZipFile) -> str | None:
    for c in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        if c in z.namelist():
            return z.read(c).decode("utf-8", "replace")
    return None


def _ids(raw: str) -> list[str]:
    ids = []
    for blk in re.finditer(r"\[\[mods\]\](.*?)(?=\n\[|\Z)", raw, re.S):
        m = re.search(r'modId\s*=\s*"([^"]+)"', blk.group(1))
        if m:
            ids.append(m.group(1))
    for grp in re.findall(r"provides\s*=\s*\[([^\]]*)\]", raw):
        ids += [p.strip().strip('"') for p in grp.split(",") if p.strip()]
    return ids


def _nested_ids(z: zipfile.ZipFile) -> list[str]:
    """modId вшитых jar — и в формате NeoForge (jarjar), и в META-INF/jars."""
    out = []
    for n in z.namelist():
        if n.endswith(".jar") and (n.startswith("META-INF/jarjar/") or n.startswith("META-INF/jars/")):
            try:
                inner = zipfile.ZipFile(io.BytesIO(z.read(n)))
                raw = _mods_toml(inner)
                if raw:
                    out += _ids(raw) + _nested_ids(inner)
            except Exception:
                pass
    return out


def read_mod(path: Path) -> ModInfo:
    with zipfile.ZipFile(path) as z:
        raw = _mods_toml(z)
        if raw is None:
            loader = "fabric" if "fabric.mod.json" in z.namelist() else "unknown"
            return ModInfo(path, None, path.stem, "", loader, None, [], [])
        ids = _ids(raw)

        def field(name):
            m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', raw, re.M)
            return m.group(1) if m else None

        requires, mc_range = [], None
        for blk in re.finditer(r"\[\[dependencies\.[^\]]+\]\](.*?)(?=\[\[|\Z)", raw, re.S):
            b = blk.group(1)
            dm = re.search(r'modId\s*=\s*"([^"]+)"', b)
            tm = re.search(r'type\s*=\s*"([^"]+)"', b)
            if not dm:
                continue
            dep = dm.group(1)
            if dep == "minecraft":
                vr = re.search(r'versionRange\s*=\s*"([^"]*)"', b)
                mc_range = vr.group(1) if vr else None
            elif (tm.group(1) if tm else "required").lower() == "required" and dep.lower() not in IGNORED_DEPS:
                requires.append(dep)

        version = field("version") or ""
        if "${" in version:  # ${file.jarVersion} — берём из имени файла
            m = re.search(r"(\d+(?:\.\d+)+[\w.+-]*)", path.stem)
            version = m.group(1) if m else ""
        return ModInfo(path, ids[0] if ids else None, field("displayName") or path.stem,
                       version, "neoforge", mc_range, requires, ids + _nested_ids(z))


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s))


def mc_range_ok(rng: str | None) -> bool:
    """Подходит ли мод под нашу версию Minecraft.

    Разбирать надо обе границы. Сначала я смотрел только нижнюю — и мод под
    1.21.1 с диапазоном «[1.21.1]» проходил проверку: 1.21.1 меньше 26.2, а
    значит «не старше нужного». Верхняя граница как раз и говорит, что выше
    этой версии мод не работает.
    """
    if not rng:
        return True
    ours = _ver(MC_VERSION)
    body = rng.strip()
    inner = body[1:-1] if body[:1] in "[(" and body[-1:] in "])" else body
    parts = [p.strip() for p in inner.split(",")]

    low = _ver(parts[0]) if parts[0] else None
    if len(parts) == 1:
        high, high_open = low, False   # «[1.21.1]» — ровно эта версия
    else:
        high = _ver(parts[1]) if parts[1] else None
        high_open = body.endswith(")")

    if low and ours < low:
        return False
    if high and (ours > high or (high_open and ours >= high)):
        return False
    return True


assert mc_range_ok("[26.2,)") and mc_range_ok("[26.1,)") and mc_range_ok(None)
assert not mc_range_ok("[1.21.1]") and not mc_range_ok("[1.21,1.22)")
assert mc_range_ok("[26.2]") and not mc_range_ok("[26.3,)")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------ поиск -------

def _builder_skip() -> dict:
    """Список исключений берём из самого сборщика, а не копируем сюда: иначе
    два списка разойдутся, и скрипт будет считать «стоящим» мод, который в
    сборку на самом деле не попадает."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("builder", REPO / "scripts" / "build-private-pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SKIP


SKIP = _builder_skip()


def installed() -> list[tuple[str, ModInfo]]:
    """(где, мод) — то, что реально попадёт в сборку: «сервер» — обязательные,
    «выбор» — выборочные. Как и сборщик, пропускаем исключённые и берём
    первую копию каждого modId (cloth-config, например, лежит в трёх папках)."""
    out, seen = [], set()
    for where, jars in (("сервер", sorted(BASE.glob("*.jar"))),
                        ("выбор", sorted(OPTIONAL.rglob("*.jar")))):
        for j in jars:
            if j.name in SKIP:
                continue
            m = read_mod(j)
            key = m.mod_id or j.name
            if key in seen:
                continue
            seen.add(key)
            out.append((where, m))
    return out


def all_copies(mod_id: str) -> list[tuple[str, Path]]:
    """Все файлы с этим modId, включая дубли — при обновлении убрать надо все,
    иначе сборщик может взять старую копию вместо новой."""
    out = []
    for where, jars in (("сервер", BASE.glob("*.jar")), ("выбор", OPTIONAL.rglob("*.jar"))):
        for j in jars:
            if read_mod(j).mod_id == mod_id:
                out.append((where, j))
    return out


def find_by_name(query: str) -> Path | None:
    """Свежайший jar, в имени которого есть запрос. Одинаковые копии (jar в
    корне проекта и он же в build/libs) считаем за один файл."""
    q = query.lower().replace(" ", "")
    hits, seen = [], set()
    for d, deep in SEARCH_DIRS:
        if not d.is_dir():
            continue
        for jar in (d.rglob("*.jar") if deep else d.glob("*.jar")):
            if q not in jar.name.lower().replace(" ", "") or ".gradle" in jar.parts:
                continue
            h = sha(jar)
            if h not in seen:
                seen.add(h)
                hits.append(jar)
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if len(hits) > 1:
        print(f"  нашлось несколько «{query}», беру самый свежий:")
        for h in hits[:4]:
            print(f"    {'→' if h == hits[0] else ' '} {time.strftime('%d.%m %H:%M', time.localtime(h.stat().st_mtime))}  {h}")
    return hits[0]


def parse_paths(line: str) -> list[str]:
    """Перетаскивание файлов в окно даёт пути в кавычках через пробел."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', line)]


# ------------------------------------------------------------ план --------

class Plan:
    """Что поменяется в папках. Хранит всё нужное для отката."""

    def __init__(self):
        self.backup = Path(tempfile.mkdtemp(prefix="stray-souls-mods-"))
        self.added: list[Path] = []                 # новые файлы — при откате удалить
        self.removed: list[tuple[Path, Path]] = []  # (где был, копия) — при откате вернуть
        self.log: list[str] = []
        self.upload: list[str] = []                 # что залить на сервер
        self.delete_on_server: list[str] = []

    def remove(self, path: Path):
        copy = self.backup / f"{len(self.removed)}_{path.name}"
        shutil.move(str(path), copy)
        self.removed.append((path, copy))

    def add(self, src: Path, dest: Path):
        shutil.copy2(src, dest)
        self.added.append(dest)

    def rollback(self):
        for p in self.added:
            p.unlink(missing_ok=True)
        for orig, copy in self.removed:
            shutil.move(str(copy), orig)


def plan_add(plan: Plan, src: Path) -> bool:
    if not src.is_file() or src.suffix.lower() != ".jar":
        print(f"  ✗ это не jar: {src}")
        return False
    mod = read_mod(src)
    if mod.loader == "fabric":
        print(f"  ✗ {src.name} — мод для Fabric, в NeoForge он не загрузится")
        return False
    if not mod.mod_id:
        print(f"  ✗ {src.name} — не похоже на мод NeoForge (нет neoforge.mods.toml)")
        return False
    if not mc_range_ok(mod.mc_range):
        print(f"  ✗ {src.name} — сделан под Minecraft {mod.mc_range}, а сборка на {MC_VERSION}")
        return False

    same = [(where, m) for where, m in installed() if m.mod_id == mod.mod_id]
    if any(sha(m.path) == sha(src) for _, m in same):
        print(f"  = {mod.name}: эта же версия уже стоит, пропускаю")
        return False

    if same:
        where, old = same[0]
        dest_dir = BASE if where == "сервер" else old.path.parent
        old_name, old_size = old.path.name, old.path.stat().st_size  # до удаления!
        for _, path in all_copies(mod.mod_id):   # все старые копии, включая дубли
            plan.remove(path)
        plan.add(src, dest_dir / src.name)
        # Свои моды часто пересобираются без смены версии — тогда «1.0.0 → 1.0.0»
        # ничего не говорит. Показываем размер и дату сборки, по ним видно,
        # что файл другой.
        if (old.version or "") == (mod.version or ""):
            stamp = time.strftime("%d.%m %H:%M", time.localtime(src.stat().st_mtime))
            change = (f"{mod.version or '?'}, новая сборка от {stamp} "
                      f"({old_size // 1024} → {src.stat().st_size // 1024} КБ)")
        else:
            change = f"{old.version or '?'} → {mod.version or '?'}"
        plan.log.append(f"обновлён {mod.name} {change}")
        print(f"  ↻ {mod.name}: {change}")
        if where == "сервер":
            plan.upload.append(src.name)
            if old_name != src.name:
                plan.delete_on_server.append(old_name)
    else:
        kind = ask(f"  {mod.name} — новый мод. Он стоит на сервере (1) или выборочный для игроков (2)? [1/2] ", {"1", "2"})
        dest_dir = BASE if kind == "1" else OPTIONAL
        plan.add(src, dest_dir / src.name)
        plan.log.append(f"добавлен {mod.name} {mod.version}".strip())
        print(f"  + {mod.name} {mod.version} → {'сервер' if kind == '1' else 'выборочные'}")
        if kind == "1":
            plan.upload.append(src.name)
    return True


def plan_remove(plan: Plan, query: str) -> bool:
    q = query.lower()
    hits = [(w, m) for w, m in installed()
            if q in (m.mod_id or "").lower() or q in m.name.lower() or q in m.path.name.lower()]
    if not hits:
        print(f"  ✗ в сборке нет ничего похожего на «{query}»")
        return False
    if len(hits) > 1:
        print(f"  под «{query}» подходит несколько:")
        for i, (w, m) in enumerate(hits, 1):
            print(f"    {i}. {m.name} ({m.path.name}, {w})")
        n = ask("  какой убрать? номер: ", {str(i) for i in range(1, len(hits) + 1)})
        hits = [hits[int(n) - 1]]
    where, m = hits[0]
    plan.remove(m.path)
    plan.log.append(f"убран {m.name}")
    print(f"  − {m.name} ({where})")
    if where == "сервер":
        plan.delete_on_server.append(m.path.name)
    return True


def check_dependencies() -> list[str]:
    """Кому из итогового набора не хватает обязательной зависимости."""
    mods = [m for _, m in installed()]
    provided = {i for m in mods for i in m.provides}
    return [f"{m.name} требует «{d}», а его в сборке нет"
            for m in mods for d in m.requires if d not in provided]


# ------------------------------------------------------------ git ---------

def git(*args, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def repo_restore():
    git("checkout", "--", *TOUCHED, check=False)
    git("clean", "-fdq", "--", *TOUCHED, check=False)


# ------------------------------------------------------------ ввод --------

def ask(prompt: str, allowed: set[str] | None = None) -> str:
    while True:
        try:
            ans = input(prompt).strip()
        except EOFError:
            sys.exit(1)
        if allowed is None or ans in allowed:
            return ans
        print(f"  нужно одно из: {', '.join(sorted(allowed))}")


def show_list():
    for title, where in (("На сервере (ставится всем):", "сервер"), ("Выборочные:", "выбор")):
        items = [m for w, m in installed() if w == where]
        print(f"\n{title} {len(items)}")
        for m in items:
            print(f"   {m.name:36} {m.version:22} {m.path.name}")


def menu() -> list[tuple[str, str]]:
    print("=" * 62)
    print("  Моды Stray Souls")
    print("=" * 62)
    print("  1 — добавить или обновить (перетащи jar в это окно или напиши название)")
    print("  2 — убрать мод")
    print("  3 — показать, что сейчас в сборке")
    print("  0 — выход")
    choice = ask("\nчто делаем? ", {"0", "1", "2", "3"})
    if choice == "0":
        sys.exit(0)
    if choice == "3":
        show_list()
        return menu()
    if choice == "1":
        line = ask("\nперетащи сюда jar (можно несколько) или напиши название: ")
        return [("add", x) for x in parse_paths(line)]
    line = ask("\nкакой мод убрать? название: ")
    return [("remove", line)]


# ------------------------------------------------------------ main --------

def run(actions: list[tuple[str, str]], dry: bool) -> int:
    dirty = git("status", "--porcelain", "--", *TOUCHED).strip()
    if dirty:
        print("\nВ папках сборки есть незакоммиченные изменения — не трогаю, чтобы их")
        print("не потерять и не опубликовать вместе с модами:")
        print(dirty)
        return 1

    plan = Plan()
    changed = False
    print()
    for op, arg in actions:
        if op == "add":
            p = Path(arg.strip().strip('"'))
            if not p.exists():
                found = find_by_name(arg)
                if not found:
                    print(f"  ✗ не нашёл «{arg}» ни по пути, ни по названию")
                    continue
                p = found
            changed |= plan_add(plan, p)
        else:
            changed |= plan_remove(plan, arg)

    if not changed:
        print("\nМенять нечего.")
        return 0

    problems = check_dependencies()
    if problems:
        print("\nВНИМАНИЕ, не хватает зависимостей — у игроков это упадёт при запуске:")
        for p in problems:
            print(f"   ✗ {p}")
        if ask("\nвсё равно продолжить? [д/н] ", {"д", "н", "y", "n"}) in {"н", "n"}:
            plan.rollback()
            print("Отменил, всё вернул как было.")
            return 1

    print("\n== пересобираю пак ==")
    build = subprocess.run([sys.executable, str(REPO / "scripts" / "build-private-pack.py")],
                           cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if build.returncode != 0:
        print(build.stdout[-2500:])
        print(build.stderr[-2500:])
        plan.rollback()
        repo_restore()
        print("\n✗ Сборка не прошла. Всё откатил: папки модов и репозиторий как были,")
        print("  в интернет ничего не ушло. Текст ошибки выше.")
        return 1
    ok_line = next((l for l in build.stdout.splitlines() if "Пак в порядке" in l), "")
    print(f"  {ok_line or 'собран'}")

    if dry:
        plan.rollback()
        repo_restore()
        print("\nПроверка прошла. Это был пробный прогон — всё откатил, ничего не опубликовано.")
        return 0

    message = "Stray Souls: " + "; ".join(plan.log)
    git("add", "-A", "--", *TOUCHED)
    git("commit", "-q", "-m", message + "\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    print("\n== публикую ==")
    try:
        git("push", "-q")
    except RuntimeError as e:
        print(f"  ✗ не удалось отправить: {e}")
        print("  Изменения сохранены локально (коммит есть). Запусти скрипт ещё раз,")
        print("  когда появится интернет, — или выполни git push сам.")
        return 1
    shutil.rmtree(plan.backup, ignore_errors=True)
    print(f"  ✓ {message}")
    print("  Игроки получат это при следующем нажатии «Играть» (Cloudflare раскладывает ~1 мин).")

    if plan.upload or plan.delete_on_server:
        print("\n⚠ Не забудь сервер билдеров — клиент и сервер должны совпадать:")
        for f in plan.upload:
            print(f"   залить:  {f}")
        for f in plan.delete_on_server:
            print(f"   удалить: {f}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    dry = "--check" in args
    args = [a for a in args if a != "--check"]

    if not args:
        actions = menu()
    elif args[0] == "list":
        show_list()
        return 0
    elif args[0] in ("add", "remove") and len(args) > 1:
        actions = [(args[0], a) for a in args[1:]]
    else:
        print(__doc__.strip())
        return 1
    return run(actions, dry)


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 1
    sys.exit(code)
