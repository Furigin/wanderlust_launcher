#!/usr/bin/env python3
"""
Собирает приватный пак 26.2 из готовых папок с модами.

    python scripts/build-private-pack.py

Откуда берутся моды:
  * база (ставится всем)      — инстанс PrismLauncher, папка mods;
  * дополнительные (на выбор) — «Новая папка (9)/моды»: файлы в корне это
    самостоятельные моды, а вложенные папки — «мод + его зависимости».

Что делает: раскладывает jar-ы в приватную папку раздачи, пишет для каждого
.pw.toml, помечает дополнительные как опциональные с русским описанием,
обновляет индекс packwiz и собирает витрину (иконки/описания/зависимости).

Приватность: и jar-ы, и метаданные лежат внутри p/<секрет>/, а не в общем
custom-mods/. Публичный манифест о сборке не знает — путь вычисляется из
пароля, см. PRIVATE_SALT в лаунчере.

Повторный запуск переписывает пак заново: правки в .pw.toml, сделанные
руками, потеряются — описания правьте в DESCRIPTIONS ниже.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKWIZ = Path.home() / "tools" / "packwiz.exe"
HOST = "https://wanderlust-launcher.ruslanyik8.workers.dev"

SALT = "wanderlust-private-v1"
PASSWORD = "sosybiby"

BASE_MODS = Path(r"C:\Users\rusla\AppData\Roaming\PrismLauncher\instances\26.2\minecraft\mods")
EXTRA_MODS = Path(r"C:\Users\rusla\Desktop\Новая папка (9)\моды")

# Cloudflare не отдаёт файлы больше 25 МБ. Такие моды раздаём с Modrinth —
# на секретность это не влияет: это публичные моды, по ссылке на них нельзя
# понять ни состав сборки, ни её адрес. Ключ — имя файла, значение — прямая
# ссылка и её sha512 (формат хеша, который отдаёт Modrinth).
BIG_FILES_FROM_MODRINTH = {
    "AmbientSounds_NEOFORGE_v6.3.6_mc26.2.jar": (
        "https://cdn.modrinth.com/data/fM515JnW/versions/odflTtI0/AmbientSounds_NEOFORGE_v6.3.6_mc26.2.jar",
    ),
}

# Моды, которые лежат в базовой папке, но раздавать их всем не нужно —
# уезжают в список «на выбор». Iris (шейдеры) и Sodium: шейдеры хотят не все,
# а на слабых машинах они только вредят. Sodium уходит сюда же не как отдельный
# пункт списка, а как обязательная зависимость Iris — он объявлен у Iris в
# neoforge.mods.toml с type = "required", витрина подхватит связь сама.
OPTIONAL_FROM_BASE = {
    "iris-neoforge-1.11.2+mc26.2.jar",
    "sodium-neoforge-0.9.1+mc26.2.jar",
}

CLOUDFLARE_FILE_LIMIT = 25 * 1024 * 1024

PACK_ID = "stray-souls"
MC_VERSION = "26.2"
NEOFORGE_VERSION = "26.2.0.75"

# Моды, которые не кладём в пак совсем, и почему.
SKIP = {
    "cbentity-1.0.0 — копия.jar": "дубликат cbentity: два jar с одним modId роняют загрузку",
    "voicechat-neoforge-2.6.20+26.2.jar": "старее, чем 2.6.21 — две версии одного мода несовместимы",
    "sodium-fabric-0.9.1+mc26.1.2.jar": "сборка Fabric и под 26.1.2 — в NeoForge 26.2 не загрузится",
    # Библиотеки, которые скачались «за компанию», но в сборке никому не нужны:
    # ни один мод их не требует и не использует их классы. Лишний вес и лишний
    # риск конфликтов.
    "architectury-neoforge-21.0.7.jar": "никому не нужен: единственный, кто его требовал, — TLib, а он тоже лишний",
    "tlib-neoforge-1.5.0-26.2.jar": "ни один мод сборки не объявляет его зависимостью и не трогает его классы",
    "KotlinLangForge-2.13.0-k2.4.10-3.1+neoforge (1).jar": "ни один мод сборки не написан на Kotlin",
    # NeoForge 26.2.0.75 переписал Entity.updateFluidInteraction: вместо
    # fluidInteraction.isInFluid(TagKey) там теперь isInFluidMatching(...),
    # а сама карта trackerByFluid переехала с TagKey на FluidType. Инжектор
    # SubtleEffects не находит точку входа и валит запуск (required = 1),
    # а если её подправить — тело метода упадёт на приведении типа.
    # 1.14.3 от 17.06.2026 — последняя сборка мода под 26.2, чинить нечем.
    "SubtleEffects-neoforge-26.2-1.14.3 (1).jar":
        "ломает запуск: инжектор в Entity.updateFluidInteraction не совпадает с NeoForge 26.2.0.75",
    "fzzy_config-0.7.6+26.2+neoforge (1).jar":
        "нужен был только SubtleEffects — без него это мёртвый вес",
}

# Русские описания для карточек. Ключ — modId.
# Всё, чего здесь нет, попадёт в список с пустым описанием.
DESCRIPTIONS = {
    "ambientsounds": "Живые звуки окружения: лес шумит, в пещерах капает, у воды слышен плеск.",
    "appleskin": "Показывает, сколько сытости и насыщения даёт еда, прямо в подсказке предмета.",
    "betteradvancements": "Удобный экран достижений: нормальный поиск, прокрутка и понятное дерево.",
    "betterclouds": "Объёмные облака вместо плоских ванильных. Небо заметно красивее, но нагружает видеокарту.",
    "chat_heads": "Рядом с сообщением в чате рисуется голова того, кто его написал.",
    "chatanimation": "Сообщения в чате появляются плавно, а не рывком.",
    "clickthrough": "Позволяет кликать сквозь таблички и рамки — не мешают взаимодействовать с блоками за ними.",
    "clientsort": "Сортировка инвентаря и сундуков одной кнопкой.",
    "durabilitytooltip": "Полоска прочности в подсказке предмета — видно износ, не наводя мышь на иконку.",
    "eg_particle_interactions": "Частицы реагируют на движение: пыль и искры разлетаются, когда проходишь рядом.",
    "elytra_physics": "Более честная физика элитр: полёт ощущается тяжелее и предсказуемее.",
    "explosiveenhancement": "Взрывы выглядят объёмнее — вместо ванильного облачка нормальная вспышка и дым.",
    "fallingleavesplus": "С деревьев опадают листья. Чистая косметика, на игру не влияет.",
    "helditemtooltips": "Название предмета в руке показывается аккуратнее, с прочностью и зачарованиями.",
    "inventoryprofilesnext": "Мощная работа с инвентарём: сортировка, автоперенос, сохранение раскладок.",
    "justzoom": "Приближение по кнопке, как в оптике. Удобно осматриваться и целиться.",
    "lambdynlights": "Динамическое освещение: факел, лава в руке и горящие мобы реально светят.",
    "mousetweaks": "Перетаскивание предметов мышью в инвентаре: раскладывать стопки заметно быстрее.",
    "particle_effects": "Дополнительные частицы для обычных действий — удары, шаги, падения.",
    "punchy": "Отдача и тряска камеры при ударах: бой чувствуется весомее.",
    "rrls": "Ресурспаки грузятся в фоне — во время загрузки можно продолжать играть, а не смотреть в экран ожидания.",
    "shulkerboxtooltip": "Показывает содержимое шалкера прямо в подсказке, не открывая его.",
    "simplefog": "Настройка тумана: можно убрать дымку вдалеке или наоборот сгустить.",
    "smoothgui": "Плавные переходы и анимации в меню и интерфейсе.",
    "smoothswapping": "Предметы в инвентаре перемещаются с анимацией, а не телепортируются.",
    "sound_physics_remastered": "Звук ведёт себя как в реальности: эхо в пещерах, глухота за стеной, реверберация.",
    "subtle_effects": "Мелкие частицы для атмосферы: искры от огня, брызги, пыль под ногами.",
    "visuality": "Больше визуальных мелочей у мобов и блоков — капли, искры, дымка.",
    "wakes": "От лодок и плавающих существ расходятся волны по воде.",
    "blur": "Размывает фон за открытым меню — интерфейс читается легче.",
    "iris": "Поддержка шейдеров: воду, тени и освещение можно сделать киношными. Требует Sodium — он включится сам. На слабой видеокарте лучше не включать.",
    "sodium": "Переписанный движок отрисовки: заметно больше FPS и меньше просадок.",
}

# Библиотеки: сами по себе игроку не нужны, в списке не показываются.
# Определяются автоматически как чужие зависимости, здесь — те, что
# автоопределение может пропустить (мод объявил зависимость мягко).
FORCE_HIDDEN = {"konkrete", "cloth_config", "architectury", "puzzleslib", "creativecore",
                "supermartijn642configlib", "libipn", "baguettelib", "fzzy_config",
                "yet_another_config_lib_v3", "tlib", "klf", "midnightlib"}


def secret_dir() -> Path:
    h = hashlib.sha256((SALT + ":" + PASSWORD).encode()).hexdigest()[:40]
    return REPO / "p" / h


def read_meta(jar: Path):
    """modId, displayName, version из jar. None, если это не мод NeoForge."""
    try:
        with zipfile.ZipFile(jar) as z:
            names = z.namelist()
            raw = None
            for c in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if c in names:
                    raw = z.read(c).decode("utf-8", "replace")
                    break
            if raw is None:
                return None, None, None
            def f(name):
                m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', raw, re.MULTILINE)
                return m.group(1) if m else None
            return f("modId"), f("displayName"), f("version")
    except Exception:
        return None, None, None


def collect():
    """Возвращает (база, дополнительные) — списки путей к jar без повторов.

    Один и тот же modId мог попасть в несколько папок (cloth_config лежит
    сразу в трёх) — берём первый и больше не повторяем.
    """
    seen = {}
    base, extra = [], []

    for jar in sorted(BASE_MODS.glob("*.jar")):
        if jar.name in SKIP:
            print(f"  пропускаю {jar.name}\n     причина: {SKIP[jar.name]}")
            continue
        mid, _, _ = read_meta(jar)
        if mid and mid in seen:
            print(f"  пропускаю {jar.name}: modId {mid} уже есть в {seen[mid]}")
            continue
        seen[mid or jar.name] = jar.name
        (extra if jar.name in OPTIONAL_FROM_BASE else base).append(jar)

    for jar in sorted(EXTRA_MODS.rglob("*.jar")):
        if jar.name in SKIP:
            print(f"  пропускаю {jar.name}\n     причина: {SKIP[jar.name]}")
            continue
        mid, _, _ = read_meta(jar)
        if mid and mid in seen:
            continue  # тот же мод из другой папки или уже в базе
        seen[mid or jar.name] = jar.name
        extra.append(jar)

    return base, extra


def toml_basic(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_entry(pack: Path, jar: Path, files_url: str, optional: bool, hidden: bool):
    mid, name, ver = read_meta(jar)
    slug = mid or re.sub(r"[^a-zA-Z0-9_-]+", "-", jar.stem).strip("-").lower()
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()

    from urllib.parse import quote
    external = BIG_FILES_FROM_MODRINTH.get(jar.name)
    download_url = external[0] if external else f"{files_url}/{quote(jar.name)}"
    body = (
        f"filename = '{jar.name}'\n"
        f"name = {toml_basic(name or slug)}\n"
        f"side = 'both'\n"
        f"x-prismlauncher-loaders = [ 'neoforge' ]\n"
        f"x-prismlauncher-mc-versions = [ '{MC_VERSION}' ]\n"
        f"x-prismlauncher-release-type = 'release'\n"
        f"x-prismlauncher-version-number = '{ver or 'custom'}'\n"
        f"\n"
    )
    if optional:
        desc = DESCRIPTIONS.get(slug, "")
        body += (
            "[option]\n"
            "optional = true\n"
            "default = false\n"
            f"description = {toml_basic(desc)}\n"
            "\n"
        )
    body += (
        "[download]\n"
        f"hash = '{digest}'\n"
        "hash-format = 'sha256'\n"
        "mode = 'url'\n"
        f"url = '{download_url}'\n"
    )
    (pack / "mods" / f"{slug}.pw.toml").write_text(body, encoding="utf-8")
    return slug


def main() -> int:
    if not BASE_MODS.is_dir():
        print(f"нет папки базовых модов: {BASE_MODS}")
        return 1
    if not EXTRA_MODS.is_dir():
        print(f"нет папки дополнительных модов: {EXTRA_MODS}")
        return 1

    secret = secret_dir()
    pack = secret / PACK_ID
    files = secret / "files"

    print("== разбираю моды ==")
    base, extra = collect()
    print(f"\nбаза: {len(base)}, дополнительные: {len(extra)}\n")

    # Пак пересобираем с нуля, чтобы не тащить записи удалённых модов.
    if (pack / "mods").is_dir():
        shutil.rmtree(pack / "mods")
    (pack / "mods").mkdir(parents=True, exist_ok=True)
    files.mkdir(parents=True, exist_ok=True)

    if not (pack / "pack.toml").is_file():
        subprocess.run(
            [str(PACKWIZ), "init", "--name", "Wanderlust Stray Souls", "--author", "furigin",
             "--version", "1.0.0", "--mc-version", MC_VERSION,
             "--modloader", "neoforge", "--neoforge-version", NEOFORGE_VERSION, "-y"],
            cwd=pack, check=True,
        )

    files_url = f"{HOST}/{files.relative_to(REPO).as_posix()}"

    for jar in base + extra:
        if jar.name in BIG_FILES_FROM_MODRINTH:
            print(f"  {jar.name}: {jar.stat().st_size // 1048576} МБ — раздаём с Modrinth")
        else:
            if jar.stat().st_size > CLOUDFLARE_FILE_LIMIT:
                print(f"  ВНИМАНИЕ: {jar.name} — {jar.stat().st_size // 1048576} МБ, "
                      f"больше лимита Cloudflare. Добавь его в BIG_FILES_FROM_MODRINTH.")
            target = files / jar.name
            if not target.exists() or target.read_bytes() != jar.read_bytes():
                shutil.copy2(jar, target)
        write_entry(pack, jar, files_url, optional=(jar in extra), hidden=False)

    subprocess.run([str(PACKWIZ), "refresh"], cwd=pack, check=True)
    print(f"\nпак собран: {pack.relative_to(REPO)}")
    print(f"файлы:      {files.relative_to(REPO)} ({len(list(files.glob('*.jar')))} jar)")
    print("\nдальше: scripts/build-mod-meta.py соберёт иконки и описания")
    return 0


if __name__ == "__main__":
    sys.exit(main())
