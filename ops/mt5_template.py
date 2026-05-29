"""MT5 bridge template discovery — config-driven, Windows + WSL aware."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

DEFAULT_EXTENSION = "tpl"
DEFAULT_CANDIDATES = ("trading_os_bridge", "trading_os")
DEFAULT_PREFERRED = "trading_os_bridge"


def load_template_config(registry=None) -> dict[str, Any]:
    if registry is None:
        from cortex.instrument_registry import load_registry

        registry = load_registry(force=True)
    cfg = dict((getattr(registry, "defaults", None) or {}).get("mt5_bridge") or {})
    candidates = [str(name).strip() for name in (cfg.get("template_candidates") or DEFAULT_CANDIDATES) if str(name).strip()]
    preferred = str(cfg.get("template_preferred") or cfg.get("template_name") or DEFAULT_PREFERRED).strip()
    extension = str(cfg.get("template_extension") or DEFAULT_EXTENSION).lstrip(".")
    if preferred and preferred not in candidates:
        candidates.insert(0, preferred)
    return {
        "template_extension": extension,
        "template_preferred": preferred,
        "template_candidates": candidates,
    }


def _appdata_roaming() -> Path | None:
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        path = Path(appdata)
        if path.exists():
            return path
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if user:
        wsl = Path(f"/mnt/c/Users/{user}/AppData/Roaming")
        if wsl.exists():
            return wsl
    return None


def terminal_roots() -> list[Path]:
    roaming = _appdata_roaming()
    if roaming is None:
        return []
    base = roaming / "MetaQuotes" / "Terminal"
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir() and p.name not in {"Common", "Community", "Help"}]


def template_filename(stem: str, extension: str = DEFAULT_EXTENSION) -> str:
    stem = str(stem).strip()
    ext = str(extension or DEFAULT_EXTENSION).lstrip(".")
    if stem.lower().endswith(f".{ext}"):
        return stem
    return f"{stem}.{ext}"


def find_template_files(stem: str, *, extension: str = DEFAULT_EXTENSION) -> list[Path]:
    name = template_filename(stem, extension)
    hits: list[Path] = []
    for root in terminal_roots():
        for rel in (
            root / "MQL5" / "Profiles" / "Templates" / name,
            root / "Profiles" / "Templates" / name,
        ):
            if rel.exists():
                hits.append(rel)
    roaming = _appdata_roaming()
    if roaming is not None:
        common = roaming / "MetaQuotes" / "Terminal" / "Common" / "Profiles" / "Templates" / name
        if common.exists():
            hits.append(common)
    return hits


def discover_templates(registry=None) -> dict[str, Any]:
    cfg = load_template_config(registry)
    extension = cfg["template_extension"]
    candidates: dict[str, list[str]] = {}
    resolved: Path | None = None
    resolved_stem: str | None = None
    for stem in cfg["template_candidates"]:
        hits = find_template_files(stem, extension=extension)
        candidates[stem] = [str(path) for path in hits]
        if hits and resolved is None:
            resolved = hits[0]
            resolved_stem = stem
    preferred = cfg["template_preferred"]
    preferred_hits = candidates.get(preferred) or []
    scan_root = _appdata_roaming()
    hint = None
    if resolved is None:
        if scan_root is None:
            hint = (
                "Template scan could not access Windows APPDATA from this shell. "
                "Run: python scripts/install_mt5_bridge_template.py (PowerShell on Windows)."
            )
        else:
            checked = ", ".join(template_filename(stem, extension) for stem in cfg["template_candidates"])
            hint = (
                f"No bridge template found (checked {checked}). "
                "Attach FileBridgeEA_MultiSymbol to EURUSD, save template, or recompile ChartBootstrapService."
            )
    elif not preferred_hits and resolved_stem:
        hint = (
            f"Using {template_filename(resolved_stem, extension)}. "
            f"Set ChartBootstrapService InpTemplateName={resolved_stem!r}."
        )
    return {
        "template_extension": extension,
        "template_preferred": preferred,
        "template_candidates": list(cfg["template_candidates"]),
        "preferred_present": bool(preferred_hits),
        "any_present": resolved is not None,
        "resolved_template": template_filename(resolved_stem, extension) if resolved_stem else None,
        "resolved_stem": resolved_stem,
        "resolved_path": str(resolved) if resolved else None,
        "paths": candidates,
        "scan_root": str(scan_root) if scan_root else None,
        "hint": hint,
    }


def preferred_template_stem(registry=None) -> str:
    return str(load_template_config(registry)["template_preferred"])


def mt5_template_status(registry=None) -> dict[str, Any]:
    return discover_templates(registry)
