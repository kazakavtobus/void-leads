"""Local-only update progress status shared by the app and external updater."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

ALLOWED_STATES = {'idle','starting','downloading','verified','backing_up',
                  'closing','installing','done','failed'}

def status_path(data_dir: Path) -> Path:
    return data_dir / 'update_progress.json'

def write_status(data_dir: Path, state: str, message: str = '') -> dict:
    if state not in ALLOWED_STATES:
        raise ValueError('Unknown update state')
    data_dir.mkdir(parents=True,exist_ok=True)
    record = {'state':state, 'message':str(message)[:400],
              'updated_at':datetime.now(timezone.utc).isoformat()}
    path = status_path(data_dir)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    tmp.replace(path)
    return record

def read_status(data_dir: Path) -> dict:
    try:
        state=json.loads(status_path(data_dir).read_text(encoding='utf-8'))
        if isinstance(state,dict) and state.get('state') in ALLOWED_STATES:
            return {k:state.get(k,'') for k in ('state','message','updated_at')}
    except (OSError,ValueError,TypeError):
        pass
    return {'state':'idle','message':'','updated_at':''}
