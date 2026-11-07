"""Kiểm receipt bằng hai tiến trình độc lập, SQLite riêng và Voice provider giả."""

import json
import os
import subprocess
import sys
from pathlib import Path

WORKER = r"""
import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import voice
from app.core.database import make_engine
from app.core.errors import AppError
from app.core.security import SEED_USER_A
from app.models import Base, CommandReceipt, PlanningDraft
from app.schemas.draft import CreateDraftRequest
from app.services import draft_service

async def main():
    engine = make_engine(sys.argv[1])
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await draft_service.ensure_dev_seed_in_db(db)
        async with factory() as db:
            try:
                if sys.argv[2] == 'draft':
                    result = await draft_service.create_draft(
                        user_id='user_a', idempotency_key='restart-draft',
                        payload=CreateDraftRequest(client_draft_id='restart-client', input_source='text', raw_input=sys.argv[3]),
                        db=db,
                    )
                    data = {'draft_id': result.draft_id, 'raw_input': result.raw_input}
                else:
                    counter = Path(sys.argv[3])
                    class FakeVoice:
                        async def transcribe(self, *args, **kwargs):
                            count = int(counter.read_text()) if counter.exists() else 0
                            counter.write_text(str(count + 1))
                            return SimpleNamespace(text='Nội dung giọng nói giả')
                    voice.get_voice_service = lambda: FakeVoice()
                    response = await voice.create_transcription(
                        purpose='uf1_input', device_id='restart-device',
                        file=UploadFile(filename='sample.wav', file=io.BytesIO(b'fake-audio')),
                        idempotency_key='restart-voice', current_user=SEED_USER_A,
                        db=db, thought_id=None,
                    )
                    data = {'text': response.data.text}
                data['receipt_count'] = await db.scalar(select(func.count()).select_from(CommandReceipt))
                data['draft_count'] = await db.scalar(select(func.count()).select_from(PlanningDraft).where(PlanningDraft.client_draft_id == 'restart-client'))
            except AppError as exc:
                data = {'error': exc.code}
        print(json.dumps(data, ensure_ascii=False))
    finally:
        await engine.dispose()

asyncio.run(main())
"""


def run_worker(tmp_path, operation, value):
    backend = Path(__file__).resolve().parents[1]
    url = "sqlite+aiosqlite:///" + (tmp_path / "receipts.sqlite3").as_posix()
    env = {
        **os.environ,
        "PYTHONPATH": str(backend),
        "PYTHONIOENCODING": "utf-8",
        "GEMINI_API_KEY": "",
        "GROQ_API_KEY": "",
        "GOOGLE_CREDENTIALS_PATH": "",
        "DATABASE_URL": url,
    }
    process = subprocess.run(
        [sys.executable, "-c", WORKER, url, operation, str(value)],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def test_draft_receipt_survives_process_restart(tmp_path):
    first = run_worker(tmp_path, "draft", "Yêu cầu ban đầu")
    replayed = run_worker(tmp_path, "draft", "Yêu cầu ban đầu")
    assert replayed == first
    assert first["receipt_count"] == 1
    assert first["draft_count"] == 1
    changed = run_worker(tmp_path, "draft", "Nội dung khác cùng mã lệnh")
    assert changed == {"error": "IDEMPOTENCY_KEY_REUSED"}


def test_voice_receipt_survives_process_restart_without_provider_retry(tmp_path):
    counter = tmp_path / "fake-provider-calls.txt"
    first = run_worker(tmp_path, "voice", counter)
    replayed = run_worker(tmp_path, "voice", counter)
    assert replayed == first
    assert first["receipt_count"] == 1
    assert first["text"] == "Nội dung giọng nói giả"
    assert counter.read_text() == "1"
