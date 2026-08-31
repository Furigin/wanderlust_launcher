#!/usr/bin/env python3
"""
Готовит витрину модов для экрана выбора в лаунчере: иконки, описания, зависимости.

    python scripts/build-mod-meta.py [--pack ИМЯ]

Что делает: для каждого опционального мода пака берёт его jar, достаёт оттуда
иконку и описание, выясняет обязательные зависимости — и складывает всё в
<пак>/mod-meta.json, а иконки в <пак>/mod-icons/. Лаунчер потом просто читает
готовый файл.

Почему из jar, а не с Modrinth: карточка тогда не зависит от внешнего API
(который в России может и не открыться), не требует ключей и не отваливается,
если мод сняли с публикации. Всё нужное — modId, displayName, description,
logoFile, dependencies — мод обязан объявить у себя в META-INF/neoforge.mods.toml.

Зависимости нужны, чтобы «включил Better Clouds → сам подтянулся YACL».
Мод, который в паке есть только ради чужой зависимости, помечается hidden:
в списке он не показывается, лаунчер включает его молча.
"""

import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CUSTOM_MODS = REPO / "custom-mods"
DEFAULT_PACK = "wanderlust-create"

# Эти зависимости объявляет каждый мод, к выбору игрока они отношения не имеют.
IGNORED_DEPS = {"minecraft", "neoforge", "forge", "fabricloader", "fabric", "java"}

# Куда ещё смотреть за jar-ами, кроме custom-mods/ (заполняется для приватного пака).
EXTRA_JAR_DIRS: list = []

# Библиотеки, которые надо прятать, даже если никто не объявил их зависимостью
# явно: игроку нечего выбирать в «Cloth Config», это просто движок настроек.
FORCE_HIDDEN = {
    "konkrete", "cloth_config", "architectury", "puzzleslib", "creativecore",
    "supermartijn642configlib", "libipn", "baguettelib", "fzzy_config",
    "yet_another_config_lib_v3", "tlib", "klf", "midnightlib", "spruceui",
    "lambdynlights_api", "yumi_mc_core",
}


def parse_pack_mods(pack: Path):
    """Читает .pw.toml пака: slug -> данные записи."""
    import tomllib

    out = {}
    for meta in sorted((pack / "mods").glob("*.pw.toml")):
        try:
            d = tomllib.load(meta.open("rb"))
        except Exception as e:
            print(f"  пропускаю {meta.name}: битый TOML ({e})")
            continue
        slug = meta.name[:-len(".pw.toml")]
        out[slug] = {
            "meta_file": f"mods/{meta.name}",
            "filename": d.get("filename", ""),
            "name": d.get("name", meta.stem),
            "side": d.get("side", "both"),
            "option": d.get("option", {}),
            "url": d.get("download", {}).get("url", ""),
        }
    return out


def load_jar(entry) -> bytes | None:
    """Байты jar: сначала локальные папки, иначе качаем по ссылке."""
    fname = entry["filename"]
    for d in [CUSTOM_MODS, *EXTRA_JAR_DIRS]:
        local = d / fname
        if local.is_file():
            return local.read_bytes()

    url = entry["url"]
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wanderlust-meta/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except Exception as e:
        print(f"  не скачался {fname}: {e}")
        return None


def read_mods_toml(jar_bytes: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(jar_bytes)) as z:
            for cand in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if cand in z.namelist():
                    return z.read(cand).decode("utf-8", "replace")
    except Exception:
        pass
    return None


def extract_info(raw: str):
    """modId, description, logoFile и список обязательных зависимостей.

    Разбираем регулярками, а не TOML-парсером: в mods.toml часто попадаются
    незакрытые кавычки и прочие вольности, из-за которых строгий парсер
    отказывается читать вполне рабочий мод.
    """
    def field(name):
        m = re.search(rf'^\s*{name}\s*=\s*"([^"]*)"', raw, re.MULTILINE)
        return m.group(1).strip() if m else None

    mod_id = field("modId")
    logo = field("logoFile")

    # description бывает как в кавычках, так и в ''' … ''' на несколько строк
    desc = None
    m = re.search(r"description\s*=\s*'''(.*?)'''", raw, re.S)
    if m:
        desc = m.group(1)
    else:
        m = re.search(r'description\s*=\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            desc = m.group(1).replace('\\n', '\n').replace('\\"', '"')
    if desc:
        desc = "\n".join(line.strip() for line in desc.strip().splitlines()).strip()

    # [[dependencies.<кто>]] с modId=... и type="required"
    deps = []
    for block in re.finditer(r"\[\[dependencies\.[^\]]+\]\](.*?)(?=\[\[|\Z)", raw, re.S):
        body = block.group(1)
        dm = re.search(r'modId\s*=\s*"([^"]+)"', body)
        tm = re.search(r'type\s*=\s*"([^"]+)"', body)
        if not dm:
            continue
        dep_id = dm.group(1)
        dep_type = (tm.group(1) if tm else "required").lower()
        if dep_type == "required" and dep_id.lower() not in IGNORED_DEPS:
            deps.append(dep_id)

    return mod_id, desc, logo, deps


def extract_icon(jar_bytes: bytes, logo: str | None, mod_id: str | None) -> bytes | None:
    """Иконка мода: logoFile из mods.toml, иначе обычные места."""
    candidates = []
    if logo:
        candidates.append(logo)
    if mod_id:
        # <modid>.png в корне — распространённый вариант у модов, которые
        # logoFile вообще не объявляют (так лежит иконка у ChatAnimation).
        candidates += [f"assets/{mod_id}/icon.png", f"assets/{mod_id}/logo.png",
                       f"{mod_id}.png"]
    candidates += ["icon.png", "logo.png", "pack.png"]

    try:
        with zipfile.ZipFile(io.BytesIO(jar_bytes)) as z:
            names = set(z.namelist())
            for c in candidates:
                if c in names:
                    return z.read(c)
            # Часть модов указывает logoFile просто по имени, а сам файл лежит
            # в assets/<modid>/textures/... — так делает, например, Iris.
            # Ищем по имени файла, а не по полному пути.
            if logo:
                base = logo.rsplit("/", 1)[-1].lower()
                for n in names:
                    if n.rsplit("/", 1)[-1].lower() == base:
                        return z.read(n)
    except Exception:
        pass
    return None


def main() -> int:
    pack_name = DEFAULT_PACK
    if "--pack" in sys.argv:
        i = sys.argv.index("--pack")
        if i + 1 < len(sys.argv):
            pack_name = sys.argv[i + 1]

    pack = REPO / pack_name
    if not (pack / "pack.toml").is_file():
        print(f"нет пака: {pack_name}")
        return 1
    # У приватного пака jar-ы лежат не в общем custom-mods/, а рядом с ним.
    global EXTRA_JAR_DIRS
    EXTRA_JAR_DIRS = [pack.parent / "files"]

    mods = parse_pack_mods(pack)
    print(f"пак {pack_name}: {len(mods)} записей")

    # Разбираем только то, что реально может попасть на экран выбора:
    # опциональные моды и всё, что они за собой тянут.
    optional = {k: v for k, v in mods.items() if v["option"].get("optional")}
    print(f"опциональных: {len(optional)}")

    icons_dir = pack / "mod-icons"
    icons_dir.mkdir(exist_ok=True)

    by_mod_id = {}   # modId -> slug
    entries = {}

    for slug, entry in optional.items():
        jar = load_jar(entry)
        if jar is None:
            print(f"  {slug}: jar недоступен, пропускаю")
            continue

        raw = read_mods_toml(jar)
        mod_id, desc, logo, deps = extract_info(raw) if raw else (None, None, None, [])
        mod_id = mod_id or slug
        by_mod_id[mod_id] = slug

        icon_data = extract_icon(jar, logo, mod_id)
        icon_name = None
        if icon_data:
            icon_name = f"{slug}.png"
            (icons_dir / icon_name).write_bytes(icon_data)

        entries[slug] = {
            "id": entry["meta_file"],          # ключ, которым лаунчер хранит выбор
            "slug": slug,
            "mod_id": mod_id,
            "name": entry["name"],
            # Короткое описание для карточки берём из пака (оно на русском),
            # полное — из мода (обычно на английском, но подробнее).
            "summary": entry["option"].get("description", ""),
            "description": desc or "",
            "icon": f"mod-icons/{icon_name}" if icon_name else None,
            "default": bool(entry["option"].get("default", False)),
            "requires_ids": deps,
            "size_bytes": len(jar),
        }

    # modId зависимостей → slug, и пометка «этот мод только ради зависимости».
    needed_by = {}
    for slug, e in entries.items():
        for dep_id in e["requires_ids"]:
            dep_slug = by_mod_id.get(dep_id)
            if dep_slug:
                needed_by.setdefault(dep_slug, []).append(slug)

    # Ссылаемся на моды тем же ключом, что и поле id (путь к .pw.toml):
    # именно им лаунчер хранит выбор игрока, и по нему же фронт ищет карточку.
    slug_to_id = {s2: e2["id"] for s2, e2 in entries.items()}
    for slug, e in entries.items():
        e["requires"] = [slug_to_id[by_mod_id[d]] for d in e["requires_ids"]
                         if d in by_mod_id and by_mod_id[d] in slug_to_id]
        e["needed_by"] = [slug_to_id[s2] for s2 in needed_by.get(slug, []) if s2 in slug_to_id]
        # Прячем всё, что кто-то тянет как зависимость: это библиотеки,
        # выбирать их отдельно игроку незачем — лаунчер включит их сам.
        # Плюс те, что перечислены явно: часть модов объявляет зависимость
        # мягко (optional), и автоопределение такую библиотеку не поймает.
        e["hidden"] = bool(e["needed_by"]) or e["mod_id"] in FORCE_HIDDEN
        e.pop("requires_ids", None)

    out = {
        "pack": pack_name,
        "mods": [entries[k] for k in sorted(entries)],
    }
    (pack / "mod-meta.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print()
    for e in out["mods"]:
        mark = "скрыт (зависимость)" if e["hidden"] else "в списке"
        req = f" | требует: {', '.join(e['requires'])}" if e["requires"] else ""
        icon = "иконка есть" if e["icon"] else "БЕЗ ИКОНКИ"
        print(f"  {e['name']:34} {mark:22} {icon}{req}")
    print(f"\n{pack_name}/mod-meta.json готов")
    print("не забудь: packwiz refresh, затем check-pack.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
