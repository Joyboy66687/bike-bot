"""Background jobs that can be scheduled independently from Telegram handlers."""
import asyncio
import logging
import subprocess
from pathlib import Path
from aiogram import types

logger = logging.getLogger(__name__)


async def offsite_backup(bot, admin_ids, backup_dir: Path, passphrase_path: Path):
    """Encrypt the newest local backup and deliver it to administrators."""
    backups = sorted(backup_dir.glob("debts_*.db"),
                     key=lambda path: path.stat().st_mtime)
    if not backups:
        logger.warning("Off-site backup skipped: no local backup exists")
        return
    if not passphrase_path.is_file():
        logger.error("Off-site backup skipped: passphrase file is missing")
        return
    encrypted_path = backups[-1].with_suffix(".db.gpg")
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["gpg", "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
             "--passphrase-file", str(passphrase_path), "-o",
             str(encrypted_path), str(backups[-1])],
            check=True, capture_output=True,
        )
        for admin_id in admin_ids:
            try:
                await bot.send_document(
                    chat_id=admin_id, document=types.FSInputFile(encrypted_path))
            except Exception:
                logger.exception("Не удалось отправить off-site бэкап админу %s",
                                 admin_id)
    except (OSError, subprocess.CalledProcessError):
        logger.exception("Не удалось зашифровать off-site бэкап")
    finally:
        encrypted_path.unlink(missing_ok=True)
