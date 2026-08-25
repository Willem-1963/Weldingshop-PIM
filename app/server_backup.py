from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = Path("/root/backup")
ERP_SOURCE_ROOT = Path("/root/weldingshop-erp")
ERP_ACTIVE_ROOT = Path("/opt/weldingshop-erp/current")
IGNORED_BACKUP_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache"}
IGNORED_BACKUP_SUFFIXES = {
    ".pyc", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".log",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60) as src:
        with sqlite3.connect(target) as dst:
            src.execute("PRAGMA busy_timeout=60000")
            src.backup(dst, pages=2048, sleep=0.05)
            result = dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite-integriteitscontrole mislukt: {source}")


def _active_pim_databases(root: Path) -> list[Path]:
    candidates = [*root.glob("data/*.sqlite3"), *root.glob("data/database/*.sqlite")]
    suppliers = root / "data" / "database" / "suppliers"
    candidates.extend(
        path for path in suppliers.glob("*.sqlite")
        if "-before-" not in path.name and "backup" not in path.name.casefold()
    )
    return sorted({path for path in candidates if path.is_file()})


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    shutil.copytree(
        source, target, symlinks=True, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "*.pyc",
            "*.sqlite", "*.sqlite3", "*.sqlite-wal", "*.sqlite-shm",
            "*.log", "backup",
        ),
    )


def _included_tree_size(source: Path) -> int:
    """Measure files that _copy_tree would include, without reading their contents."""
    if not source.exists():
        return 0
    total = 0
    for path in source.rglob("*"):
        try:
            relative_parts = path.relative_to(source).parts
            if any(part in IGNORED_BACKUP_NAMES for part in relative_parts):
                continue
            if path.is_file() and not path.is_symlink():
                if path.name == "weldingshop-backups":
                    continue
                if any(path.name.endswith(suffix) for suffix in IGNORED_BACKUP_SUFFIXES):
                    continue
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _repository_bundle_estimate(repository: Path) -> int:
    git_dir = repository / ".git"
    if not git_dir.exists():
        return 0
    return sum(
        path.stat().st_size
        for path in git_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def estimate_server_backup_size(
    *, pim_root: str | Path = PROJECT_ROOT,
    erp_source_root: str | Path = ERP_SOURCE_ROOT,
    erp_active_root: str | Path = ERP_ACTIVE_ROOT,
    include_server_config: bool = True,
) -> dict[str, int]:
    """Estimate archive and temporary workspace sizes from current source files."""
    pim = Path(pim_root).resolve()
    erp_source = Path(erp_source_root).resolve()
    erp_active = Path(erp_active_root).resolve()
    source_bytes = sum(path.stat().st_size for path in _active_pim_databases(pim))
    erp_database = erp_active / "data" / "weldingshop_erp.sqlite3"
    if erp_database.is_file():
        source_bytes += erp_database.stat().st_size
    source_bytes += _repository_bundle_estimate(pim)
    source_bytes += _repository_bundle_estimate(erp_source)
    for directory in (pim / "app", pim / "scripts", pim / "config"):
        source_bytes += _included_tree_size(directory)
    for name in ("audit", "blog_assets", "content", "imports", "output", "product_maker_uploads"):
        source_bytes += _included_tree_size(pim / "data" / name)
    source_bytes += _included_tree_size(erp_active / "data")
    for filename in (".env", "VERSION", "BUILD", "BUILD.txt"):
        path = erp_active / filename
        if path.is_file():
            source_bytes += path.stat().st_size
    if include_server_config:
        for path in (Path("/etc/systemd/system"), Path("/etc/nginx"), Path("/etc/letsencrypt")):
            source_bytes += _included_tree_size(path)
    # Gzip usually reduces this substantially. Five percent allows for tar and
    # encryption overhead and intentionally presents a conservative upper estimate.
    archive_upper_bytes = int(source_bytes * 1.05) + 1024 * 1024
    return {
        "source_bytes": source_bytes,
        "archive_upper_bytes": archive_upper_bytes,
        # Staging copy + plaintext archive + encrypted archive coexist briefly.
        "temporary_required_bytes": source_bytes + (2 * archive_upper_bytes),
    }


def _git_bundle(repository: Path, target: Path) -> dict[str, Any]:
    if not (repository / ".git").exists():
        return {"repository": str(repository), "included": False}
    target.parent.mkdir(parents=True, exist_ok=True)
    git_command = ["git", "-c", f"safe.directory={repository}"]
    subprocess.run(
        [*git_command, "bundle", "create", str(target), "--all"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    commit = subprocess.run(
        [*git_command, "rev-parse", "HEAD"], cwd=repository, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        [*git_command, "status", "--porcelain"], cwd=repository, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"Werkmap bevat niet-vastgelegde wijzigingen: {repository}")
    return {"repository": str(repository), "included": True, "commit": commit}


def _write_restore_guide(path: Path) -> None:
    path.write_text(
        """# Weldingshop noodherstel

Dit archief bevat vertrouwelijke bedrijfsdata en secrets. Bewaar het wachtwoord
apart van het back-upbestand.

1. Controleer eerst de SHA-256 naast het versleutelde archief.
2. Ontsleutel op een afgeschermde herstelserver:
   `openssl enc -d -aes-256-cbc -pbkdf2 -iter 250000 -in BACKUP.enc -out BACKUP.tar.gz`
3. Pak het archief uit en controleer `MANIFEST.json` en `CHECKSUMS.sha256`.
4. Installeer PIM- en ERP-code vanuit de Git-bundels of de meegeleverde bronkopie.
5. Stop PIM en ERP vóór databases worden teruggezet.
6. Zet SQLite-snapshots terug op de paden uit `MANIFEST.json`; zet daarna
   configuratie, secrets en serverconfiguratie terug met de oorspronkelijke rechten.
7. Start eerst ERP, daarna PIM, en voer healthchecks en steekproeven uit.

Voer een echte restore nooit rechtstreeks over een draaiende productieserver uit.
Herstel eerst op een aparte machine of in een afzonderlijke map.
""",
        encoding="utf-8",
    )


def create_server_backup(
    password: str, *, backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    pim_root: str | Path = PROJECT_ROOT, erp_source_root: str | Path = ERP_SOURCE_ROOT,
    erp_active_root: str | Path = ERP_ACTIVE_ROOT,
    include_server_config: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create one encrypted, checksummed and restore-oriented server backup."""
    if len(password) < 12:
        raise ValueError("Gebruik een back-upwachtwoord van minimaal 12 tekens")
    notify = progress or (lambda message: None)
    backup_root = Path(backup_dir)
    backup_root.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_root, 0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"weldingshop-server-backup-{timestamp}"
    encrypted = backup_root / f"{name}.tar.gz.enc"
    checksum_path = backup_root / f"{name}.sha256"
    if encrypted.exists():
        raise RuntimeError("Back-upnaam bestaat al; probeer over één seconde opnieuw")
    pim = Path(pim_root).resolve()
    erp_source = Path(erp_source_root).resolve()
    erp_active = Path(erp_active_root).resolve()

    with tempfile.TemporaryDirectory(prefix=".building-", dir=backup_root) as temp:
        staging = Path(temp) / name
        staging.mkdir()
        notify("Consistente databasesnapshots maken…")
        databases = []
        for source in _active_pim_databases(pim):
            relative = source.relative_to(pim)
            target = staging / "sqlite" / "pim" / relative
            _sqlite_backup(source, target)
            databases.append({"source": str(source), "archive": str(target.relative_to(staging))})
        erp_database = erp_active / "data" / "weldingshop_erp.sqlite3"
        if erp_database.is_file():
            target = staging / "sqlite" / "erp" / "data" / erp_database.name
            _sqlite_backup(erp_database, target)
            databases.append({"source": str(erp_database), "archive": str(target.relative_to(staging))})

        notify("Broncode en Git-historie vastleggen…")
        git_info = [
            _git_bundle(pim, staging / "git" / "weldingshop-pim.bundle"),
            _git_bundle(erp_source, staging / "git" / "weldingshop-erp.bundle"),
        ]
        _copy_tree(pim / "app", staging / "files" / "pim" / "app")
        _copy_tree(pim / "scripts", staging / "files" / "pim" / "scripts")
        _copy_tree(pim / "config", staging / "files" / "pim" / "config")

        notify("Productbestanden, imports en documenten kopiëren…")
        for directory in ("audit", "blog_assets", "content", "imports", "output", "product_maker_uploads"):
            _copy_tree(pim / "data" / directory, staging / "files" / "pim" / "data" / directory)
        _copy_tree(erp_active / "data", staging / "files" / "erp" / "data")
        for filename in (".env", "VERSION", "BUILD", "BUILD.txt"):
            source = erp_active / filename
            if source.is_file():
                target = staging / "files" / "erp" / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

        notify("Serverconfiguratie en herstelgegevens verzamelen…")
        if include_server_config:
            for source, label in (
                (Path("/etc/systemd/system"), "systemd"),
                (Path("/etc/nginx"), "nginx"),
                (Path("/etc/letsencrypt"), "letsencrypt"),
            ):
                _copy_tree(source, staging / "files" / "server" / label)
        manifest = {
            "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname": os.uname().nodename, "encrypted": True,
            "encryption": "AES-256-CBC PBKDF2 SHA-256 250000 iterations",
            "databases": databases, "git": git_info,
            "pim_root": str(pim), "erp_source_root": str(erp_source),
            "erp_active_root": str(erp_active),
        }
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_restore_guide(staging / "RESTORE.md")
        checksum_lines = []
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            checksum_lines.append(f"{_sha256(path)}  {path.relative_to(staging)}")
        (staging / "CHECKSUMS.sha256").write_text(
            "\n".join(checksum_lines) + "\n", encoding="utf-8"
        )

        notify("Archief comprimeren en versleutelen…")
        plaintext = Path(temp) / f"{name}.tar.gz"
        with tarfile.open(plaintext, "w:gz") as archive:
            archive.add(staging, arcname=name, recursive=True)
        try:
            subprocess.run(
                ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
                 "-iter", "250000", "-md", "sha256", "-pass", "stdin",
                 "-in", str(plaintext), "-out", str(encrypted)],
                input=password + "\n", text=True, check=True,
                capture_output=True,
            )
        finally:
            plaintext.unlink(missing_ok=True)
    os.chmod(encrypted, 0o600)
    checksum = _sha256(encrypted)
    checksum_path.write_text(f"{checksum}  {encrypted.name}\n", encoding="utf-8")
    os.chmod(checksum_path, 0o600)
    return {
        "path": str(encrypted), "checksum_path": str(checksum_path),
        "sha256": checksum, "size": encrypted.stat().st_size,
        "created_at": manifest["created_at"], "database_count": len(databases),
    }


def list_server_backups(backup_dir: str | Path = DEFAULT_BACKUP_DIR) -> list[dict[str, Any]]:
    root = Path(backup_dir)
    if not root.exists():
        return []
    result = []
    for path in sorted(root.glob("weldingshop-server-backup-*.tar.gz.enc"), reverse=True):
        checksum_file = path.with_suffix("").with_suffix("").with_suffix(".sha256")
        expected = ""
        if checksum_file.is_file():
            expected = checksum_file.read_text(encoding="utf-8").split()[0]
        result.append({
            "name": path.name, "path": str(path), "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "sha256": expected, "verified": False,
            "checksum_path": str(checksum_file) if checksum_file.exists() else "",
        })
    return result


def backup_storage_summary(
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
) -> dict[str, int]:
    """Return archive usage and filesystem capacity for the backup location."""
    root = Path(backup_dir)
    disk_probe = root if root.exists() else root.parent
    while not disk_probe.exists() and disk_probe != disk_probe.parent:
        disk_probe = disk_probe.parent
    disk = shutil.disk_usage(disk_probe)
    backups = list_server_backups(root)
    backup_bytes = sum(item["size"] for item in backups)
    checksum_bytes = sum(
        Path(item["checksum_path"]).stat().st_size
        for item in backups
        if item["checksum_path"] and Path(item["checksum_path"]).is_file()
    )
    return {
        "backup_count": len(backups),
        "backup_bytes": backup_bytes + checksum_bytes,
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
    }


def verify_server_backup(
    path: str | Path, *, backup_dir: str | Path = DEFAULT_BACKUP_DIR,
) -> dict[str, Any]:
    archive = Path(path).resolve()
    if archive.parent != Path(backup_dir).resolve() or not archive.is_file():
        raise ValueError("Onbekend serverback-upbestand")
    checksum_file = archive.with_suffix("").with_suffix("").with_suffix(".sha256")
    if not checksum_file.is_file():
        raise ValueError("SHA-256-bestand ontbreekt")
    expected = checksum_file.read_text(encoding="utf-8").split()[0]
    actual = _sha256(archive)
    return {"valid": expected == actual, "expected": expected, "actual": actual}
