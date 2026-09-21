"""Single-worker FastAPI service; private artifacts never leave public endpoints."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import Field, HttpUrl, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from qab import __version__
from qab.report import corpus_fingerprint, evaluate
from qab.schema import Strict, Submission, SubmissionMeta
from qab.scoring import score_case
from qab.tasks import MODELS, PRIMARY, TASK_VERSION, evaluate_task, fingerprint as task_fingerprint

from .data import CONFIG, load_bundle
from .drafts import DraftHandoffs
from .storage import Store

MAX_BYTES = 20 * 1024 * 1024
ROOT = Path(__file__).parent


class Metadata(Strict):
    system: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=2000)
    url: HttpUrl
    email: str = Field(max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    user_parameters: int = Field(ge=0, le=10000)
    hardware_class: Literal['cpu', 'gpu'] | None = None
    hardware: str = Field(default='', max_length=200)
    tasks: list[Literal['alignment', 'segmentation', 'timing']] = Field(default_factory=lambda: ['alignment'], min_length=1, max_length=3)

    @field_validator('system')
    @classmethod
    def clean_name(cls, value):
        value = ' '.join(unicodedata.normalize('NFKC', value).split())
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Use a visible system name')
        return value

    @property
    def key(self):
        return self.system.casefold()

    def scorer_meta(self, version='web'):
        return SubmissionMeta(system=self.system, version=version, hardware_class=self.hardware_class,
                              hardware=self.hardware or None)

    def public(self):
        return self.model_dump(mode='json', exclude={'email'})


def errors(exc):
    if isinstance(exc, json.JSONDecodeError):
        return f'Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}. Fix the JSON and upload again.'
    if isinstance(exc, ValidationError):
        messages = []
        for e in exc.errors():
            loc = e['loc']
            field = str(loc[-1]) if loc else ''
            prefix = f'Segment {loc[1] + 1}, {field}: ' if len(loc) > 1 and loc[0] == 'segments' else (
                f'{field}: ' if field else '')
            if len(loc) > 1 and loc[0] == 'clips':
                prefix = f'Clip {loc[1] + 1}'
                if 'words' in loc:
                    index = loc.index('words') + 1
                    word_index = next((v for v in loc[index:] if isinstance(v, int)), None)
                    if word_index is not None:
                        prefix += f', word {word_index + 1}'
                prefix += f', {field}: '
            message = e['msg'].removeprefix('Value error, ').replace('chapter', 'surah')
            value = e.get('input')
            if field == 'reference' and isinstance(value, str):
                if re.fullmatch(r'\d+:\d+:\d+', value):
                    message = (f'Single-word shorthand "{value}" is not supported. '
                               f'Use "{value}-{value}" instead (surah:ayah:word at both ends).')
                elif re.fullmatch(r'\d+:\d+(?:-\d+:\d+)?', value):
                    message = (f'Verse-only shorthand "{value}" is not supported. Include word numbers at both '
                               'ends, for example "84:1:1-84:1:3" for all of verse 84:1.')
                elif 'out of range' in message:
                    message += '. Check the surah, ayah and 1-based word numbers against the Hafs dataset.'
                elif 'end precedes' in message:
                    message += '. Keep each span in reading order. Represent a repeat with a new timed segment.'
                elif 'one surah' in message:
                    message += '. Split this claim into a separate timed segment for each surah.'
                else:
                    message += '. Use "S:A:W-S:A:W", "Basmala", "Isti\'adha", or JSON null (without quotes).'
            elif field == 'confidence':
                message = 'Use a finite number from 0 to 1, or omit confidence consistently across the submission.'
            elif field == 'runtime_seconds':
                message = 'Use a positive processing time in seconds for every recording, or omit runtime everywhere.'
            elif field == 'email':
                message = 'Enter a valid contact email, for example name@example.org. It stays private.'
            elif field == 'url':
                message = 'Enter a full website or repository URL starting with https:// or http://.'
            elif e['type'] == 'extra_forbidden':
                message = f'Unknown field "{field}". Remove it; see the selected task format for supported fields.'
            elif e['type'] == 'missing':
                message = f'Add the required "{field}" field.'
            if 'segment end must follow' in message:
                message += '. Set end_s greater than start_s; times are seconds from the recording start.'
            if 'confidence is all-or-nothing' in message:
                message += '. Add confidence to every Quran-span and Basmala segment, or remove it from all of them.'
            if 'overlap by more' in message:
                match = re.search(r'segments (\d+) and (\d+)', message)
                if match:
                    message = (f'Segments {int(match[1]) + 1} and {int(match[2]) + 1} overlap by more than 0.5 s. '
                               'Adjust their boundaries so the overlap is at most 0.5 seconds.')
            if 'ordered by start_s' in message:
                match = re.search(r'segment (\d+)', message)
                message = (f'Segment {int(match[1]) + 1} starts before the preceding segment. '
                           'Sort the segments by start_s.') if match else message
            messages.append(prefix + message)
        return '\n'.join(messages)
    return str(exc)


def unpack(files):
    entries = []
    size = 0
    for filename, raw in files:
        if filename.lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = [i for i in archive.infolist() if not i.is_dir()]
                if len(infos) > 500:
                    raise ValueError('ZIP contains too many files')
                for info in infos:
                    if info.flag_bits & 1:
                        raise ValueError('Encrypted ZIP files are not supported')
                    size += info.file_size
                    if size > MAX_BYTES:
                        raise ValueError('Expanded uploads exceed 20 MB')
                    entries.append((info.filename, archive.read(info)))
        else:
            size += len(raw)
            if size > MAX_BYTES:
                raise ValueError('Uploads exceed 20 MB')
            entries.append((filename, raw))
    if len(entries) > 500:
        raise ValueError('Too many files')
    return entries


def validate_files(files, cases, meta):
    task = meta.tasks[0]
    by_id = {c.id: c for c in cases}
    valid, issues, seen = {}, {}, set()
    for filename, raw in unpack(files):
        path = PurePosixPath(filename.replace('\\', '/'))
        case_id = path.stem
        if path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.json':
            issues[filename] = 'Expected <recording-id>.json'
            continue
        if case_id not in by_id:
            issues[filename] = 'Unknown recording ID. Upload only prediction JSON files for this corpus.'
            continue
        if case_id in seen:
            issues[case_id] = 'Duplicate recording ID in this upload; keep exactly one file.'
            valid.pop(case_id, None)
            continue
        seen.add(case_id)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Expected an object containing segments')
            payload = dict(payload)
            payload.setdefault('case_id', case_id)
            if payload['case_id'] != case_id:
                raise ValueError('case_id does not match the filename')
            sub = (Submission if task == 'alignment' else MODELS[task]).model_validate(payload)
            if task != 'alignment':
                evaluate_task(task, [by_id[case_id]], [sub], meta.scorer_meta())
                valid[case_id] = sub
                continue
            for i, seg in enumerate(sub.segments, 1):
                if seg.end_s > by_id[case_id].duration_s + 1.0:
                    raise ValueError(f'Segment {i}: end_s={seg.end_s:g} exceeds this recording\'s duration '
                                     f'({by_id[case_id].duration_s:g} s) plus the 1 s allowance. '
                                     'Use seconds from the start of this recording, not milliseconds.')
            if len(sub.segments) > 100000:
                raise ValueError('Too many segments')
            score_case(by_id[case_id], sub)
            valid[case_id] = sub
        except (ValueError, TypeError) as exc:
            issues[case_id] = errors(exc)
    overall = []
    subs = list(valid.values())
    runtimes = [getattr(s, 'runtime_seconds', None) is not None for s in subs]
    confidence = [s.confidence_reported for s in subs if any(seg.may_claim_quran for seg in s.segments)] if task == 'alignment' else []
    if any(runtimes) and not all(runtimes):
        overall.append('Runtime must be provided for every recording or none.')
    if any(runtimes) and (not meta.hardware_class or not meta.hardware.strip()):
        overall.append('Runtime reporting requires a hardware class and hardware description.')
    if any(confidence) and not all(confidence):
        overall.append('Confidence must be provided for every eligible segment across all recordings or none.')
    report = None
    if subs and not overall:
        report = (evaluate([c for c in cases if c.id in valid], subs, meta.scorer_meta(), CONFIG['latest'])
                  if task == 'alignment' else evaluate_task(task, [c for c in cases if c.id in valid],
                                                           subs, meta.scorer_meta(), CONFIG['latest']))
    statuses = [{'id': c.id, 'status': 'invalid' if c.id in issues else 'valid' if c.id in valid else 'missing',
                 'error': issues.get(c.id)} for c in cases]
    return subs, {'recordings': statuses, 'file_errors': {k: v for k, v in issues.items() if k not in by_id},
                  'errors': overall, 'valid': len(valid), 'total': len(cases),
                  'complete': len(valid) == len(cases) and not issues and not overall,
                  'scores': report['pooled'] if report else None, 'task': task,
                  'report': report if task != 'alignment' else None}


def digest(meta, subs):
    body = {'metadata': meta.model_dump(mode='json'),
            'predictions': [s.model_dump(mode='json') for s in sorted(subs, key=lambda s: s.case_id)]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()


def verify_hf_token(token: str) -> tuple[str, str]:
    """Resolve a Hugging Face user token to a stable private owner identity."""
    try:
        identity = HfApi().whoami(token=token)
    except HfHubHTTPError as exc:
        status = getattr(exc.response, 'status_code', None)
        if status in (401, 403):
            raise HTTPException(401, 'Invalid Hugging Face token',
                                headers={'WWW-Authenticate': 'Bearer'}) from None
        raise HTTPException(503, 'Hugging Face identity service is unavailable') from None
    except Exception:
        raise HTTPException(503, 'Hugging Face identity service is unavailable') from None
    owner = identity.get('id')
    username = identity.get('name')
    if not isinstance(owner, str) or not owner or not isinstance(username, str) or not username:
        raise HTTPException(401, 'Hugging Face token has no user identity',
                            headers={'WWW-Authenticate': 'Bearer'})
    return owner, username


async def bearer_identity(request: Request) -> tuple[str, str]:
    value = request.headers.get('authorization', '')
    scheme, separator, token = value.partition(' ')
    if scheme.lower() != 'bearer' or not separator or not token or len(token) > 512:
        raise HTTPException(401, 'Use Authorization: Bearer <hf-token>',
                            headers={'WWW-Authenticate': 'Bearer'})
    return await run_in_threadpool(verify_hf_token, token)


def create_app(cases_override=None, store_override=None):
    secret = os.environ.get('SESSION_SECRET') or secrets.token_hex(32)
    signer = URLSafeTimedSerializer(secret, salt='qab-preview')
    handoffs = DraftHandoffs()
    corpus_lock = threading.Lock()
    refreshed = {}
    publish_lock = threading.Lock()
    compute_lock = threading.Semaphore(2)
    state = {'cases': {}, 'metadata': {}, 'records': [], 'ready': False}

    @asynccontextmanager
    async def lifespan(app):
        state['store'] = store_override or Store(os.getenv('QAB_STORE_DIR'), os.getenv('QAB_BUCKET'))
        for version in CONFIG['versions']:
            if cases_override:
                state['cases'][version] = cases_override
                state['metadata'][version] = []
            else:
                state['cases'][version], state['metadata'][version] = await run_in_threadpool(
                    load_bundle, version, os.getenv('QAB_CASES_DIR'))
        refreshed.update({version: time.monotonic() for version in CONFIG['versions']})
        state['records'] = await run_in_threadpool(state['store'].records)
        state['ready'] = True
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    secure = bool(os.getenv('SPACE_HOST'))
    app.add_middleware(SessionMiddleware, secret_key=secret, https_only=secure,
                       same_site='none' if secure else 'lax', max_age=8 * 3600)
    oauth = OAuth()
    if os.getenv('OAUTH_CLIENT_ID'):
        oauth.register('hf', client_id=os.environ['OAUTH_CLIENT_ID'],
                       client_secret=os.environ['OAUTH_CLIENT_SECRET'],
                       server_metadata_url='https://huggingface.co/.well-known/openid-configuration',
                       client_kwargs={'scope': 'openid profile', 'code_challenge_method': 'S256'})

    @app.middleware('http')
    async def limits(request, call_next):
        from fastapi.responses import JSONResponse
        if request.method == 'POST':
            if request.headers.get('x-qab-request') != '1':
                return JSONResponse({'detail': 'Missing request protection header'}, status_code=403)
            try:
                if int(request.headers.get('content-length', MAX_BYTES + 1)) > MAX_BYTES:
                    return JSONResponse({'detail': 'Upload limit is 20 MB'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if request.url.path.startswith(('/api/', '/auth/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def corpus(version):
        if version not in state['cases']:
            raise HTTPException(404, 'Unknown corpus version')
        if not cases_override and not os.getenv('QAB_CASES_DIR') and time.monotonic() - refreshed[version] >= 60:
            with corpus_lock:
                if time.monotonic() - refreshed[version] >= 60:
                    cases, metadata = load_bundle(version)
                    state['cases'][version], state['metadata'][version] = cases, metadata
                    refreshed[version] = time.monotonic()
                    filtered.cache_clear()
        return state['cases'][version]

    def current(key, version, profile=None, task='alignment'):
        found = [r for r in state['records'] if r['key'] == key and r['corpus_version'] == version
                 and r['metadata'].get('hardware_class') == profile
                 and r['metadata'].get('tasks', ['alignment']) == [task]]
        return max(found, key=lambda r: (r['created_at'], r['id'])) if found else None

    @lru_cache(maxsize=256)
    def filtered(record_id, ids):
        record = next(r for r in state['records'] if r['id'] == record_id)
        cases = [c for c in corpus(record['corpus_version']) if c.id in ids]
        meta = Metadata.model_validate(record['metadata'])
        task = meta.tasks[0]
        model = Submission if task == 'alignment' else MODELS[task]
        subs = [model.model_validate(s) for s in record['predictions'] if s['case_id'] in ids]
        report = (evaluate(cases, subs, meta.scorer_meta(record['id']), record['corpus_version'])
                  if task == 'alignment' else evaluate_task(task, cases, subs, meta.scorer_meta(record['id']),
                                                           record['corpus_version']))
        return report['pooled']

    @app.get('/healthz')
    def health():
        return {'ready': state['ready'], 'scorer_version': __version__}

    @app.get('/api/session')
    def session(request: Request):
        return {'signed_in': bool(request.session.get('sub')), 'oauth_available': bool(os.getenv('OAUTH_CLIENT_ID'))}

    @app.post('/api/draft-handoff')
    async def save_handoff(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get('profiles'), dict) or not isinstance(data.get('metadata'), dict):
            raise HTTPException(422, 'Invalid draft')
        return {'token': handoffs.put(data)}

    @app.post('/api/draft-resume')
    async def resume_handoff(request: Request):
        data = await request.json()
        token = data.get('token') if isinstance(data, dict) else None
        if not isinstance(token, str) or len(token) != 43:
            raise HTTPException(400, 'Invalid draft handoff')
        return handoffs.get(token)

    @app.get('/auth/login')
    async def login(request: Request, resume: str | None = None):
        if not os.getenv('OAUTH_CLIENT_ID'):
            raise HTTPException(503, 'Hugging Face sign-in is available on the hosted Space')
        if resume:
            handoffs.get(resume)
            request.session['draft_resume'] = resume
        else:
            request.session.pop('draft_resume', None)
        host = os.environ.get('SPACE_HOST')
        redirect = f'https://{host}/auth/callback' if host else str(request.url_for('callback'))
        return await oauth.hf.authorize_redirect(request, redirect)

    @app.get('/auth/callback')
    async def callback(request: Request):
        resume = request.session.get('draft_resume')
        destination = '/#submit' + ('?resume=' + resume if resume else '')
        try:
            token = await oauth.hf.authorize_access_token(request)
            user = token['userinfo']
            request.session.clear()
            request.session.update(sub=user['sub'], username=user.get('preferred_username', ''))
        except Exception:
            return RedirectResponse(destination + ('&' if resume else '?') + 'auth_error=1')
        return RedirectResponse(destination)

    @app.post('/auth/logout')
    def logout(request: Request):
        request.session.clear()
        return {'signed_in': False}

    @app.get('/api/corpus')
    def dataset(version: str = CONFIG['latest']):
        cases = corpus(version)
        rows = []
        for c in cases:
            quran = [w for w in c.words if re.match(r'^\d+:', w.word)]
            repeats = sum(a.token[1] == b.token[1] and b.token[2] <= a.token[2]
                          for a, b in zip(quran, quran[1:]))
            rows.append({**c.model_dump(exclude={'words', 'non_quran', 'segments', 'schema_version'}),
                         'word_count': len(quran), 'repeats': repeats,
                         'chapters': sorted({int(w.word.split(':')[0]) for w in quran})})
        if state['metadata'][version]:
            rows = state['metadata'][version]
        else:
            for row in rows:
                row.update(passages='', recited_words=row['word_count'], repeat_events=row['repeats'], wpm=None)
        return {'latest': CONFIG['latest'], 'version': version, 'versions': list(CONFIG['versions']),
                'scorer_version': __version__,
                'fingerprint': corpus_fingerprint(cases), 'recordings': rows}

    @app.get('/api/leaderboard')
    def board(version: str = CONFIG['latest'], task: Literal['alignment', 'segmentation', 'timing'] = 'alignment', ids: Annotated[list[str] | None, Query()] = None):
        cases = corpus(version)
        selected = tuple(sorted(ids if ids is not None else [c.id for c in cases]))
        if not selected or set(selected) - {c.id for c in cases}:
            raise HTTPException(422, 'Select known recordings')
        rows = []
        profiles = {(r['key'], r['metadata'].get('hardware_class')) for r in state['records']}
        for key, profile in sorted(profiles, key=lambda item: (item[0], item[1] or '')):
            record = current(key, version, profile, task)
            if record:
                meta = Metadata.model_validate(record['metadata'])
                rows.append({'id': record['id'], **meta.public(), 'synthetic': record.get('synthetic', False),
                             'display_name': meta.system + (f' ({profile.upper()})' if profile else ''),
                             'submitted_at': record['created_at'],
                             'task': task, 'corpus_version': record['corpus_version'],
                             'recording_count': len(record['predictions']),
                             'scorer_version': record['scorer_version'],
                             'scores': filtered(record['id'], selected)})
        rows.sort(key=lambda r: (-(r['scores'][PRIMARY[task]] or 0), r['system'].casefold()))
        for row in rows:
            row['rank'] = 1 + sum((r['scores'][PRIMARY[task]] or 0) > (row['scores'][PRIMARY[task]] or 0) for r in rows)
        return {'version': version, 'selected': len(selected), 'rows': rows}

    async def parsed(files, metadata):
        try:
            metadata_errors = []
            try:
                meta = Metadata.model_validate_json(metadata)
                if len(meta.tasks) != 1:
                    raise ValueError('Use the combined submission endpoint for multiple tasks.')
            except ValidationError as exc:
                metadata_errors.append(errors(exc))
                meta = Metadata(system='Preview', description='Private score preview',
                                url='https://example.org', email='preview@example.org', user_parameters=0,
                                tasks=[json.loads(metadata).get('tasks', ['alignment'])[0]]
                                if json.loads(metadata).get('tasks') in [['alignment'], ['segmentation'], ['timing']]
                                else ['alignment'])
            raw = []
            total = 0
            for f in files:
                content = await f.read(MAX_BYTES + 1)
                total += len(content)
                if total > MAX_BYTES:
                    raise ValueError('Uploads exceed 20 MB')
                raw.append((f.filename or '', content))
            if not compute_lock.acquire(blocking=False):
                raise HTTPException(429, 'Scoring is busy. Please retry shortly.')
            try:
                subs, result = await run_in_threadpool(validate_files, raw, corpus(CONFIG['latest']), meta)
            finally:
                compute_lock.release()
            result['errors'].extend(metadata_errors)
            if metadata_errors:
                result['complete'] = False
            return meta, subs, result
        except (ValueError, TypeError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, errors(exc)) from None

    @app.post('/api/preview')
    async def preview(request: Request, files: Annotated[list[UploadFile], File()],
                      metadata: Annotated[str, Form()]):
        meta, subs, result = await parsed(files, metadata)
        old = current(meta.key, CONFIG['latest'], meta.hardware_class, meta.tasks[0])
        owners = {r['owner_sub'] for r in state['records'] if r['key'] == meta.key}
        result['replacement'] = bool(old)
        result['owned_by_another'] = bool(owners and request.session.get('sub') not in owners)
        result['token'] = signer.dumps({'hash': digest(meta, subs), 'version': CONFIG['latest'],
                                        'fingerprint': task_fingerprint(corpus(CONFIG['latest'])),
                                        'previous': old['id'] if old else None}) if result['complete'] else None
        return result

    @app.post('/api/publish')
    async def publish(request: Request, files: Annotated[list[UploadFile], File()],
                      metadata: Annotated[str, Form()], preview_token: Annotated[str, Form()],
                      confirmed: Annotated[bool, Form()] = False):
        owner = request.session.get('sub')
        if not owner:
            raise HTTPException(401, 'Sign in with Hugging Face before publishing')
        if not confirmed:
            raise HTTPException(422, 'Review the results and confirm publication')
        meta, subs, result = await parsed(files, metadata)
        try:
            proof = signer.loads(preview_token, max_age=3600)
        except BadSignature:
            raise HTTPException(409, 'Preview expired. Review your results again.') from None
        if not result['complete'] or proof['hash'] != digest(meta, subs) or proof['version'] != CONFIG['latest'] or proof.get('fingerprint') != task_fingerprint(corpus(CONFIG['latest'])):
            raise HTTPException(409, 'Files or details changed. Review your results again.')

        def commit():
            with publish_lock:
                owners = {r['owner_sub'] for r in state['records'] if r['key'] == meta.key}
                if owners and owner not in owners:
                    raise HTTPException(403, 'This system name belongs to another account')
                old = current(meta.key, CONFIG['latest'], meta.hardware_class, meta.tasks[0])
                if (old['id'] if old else None) != proof['previous']:
                    raise HTTPException(409, 'This system changed since preview. Review again before replacing it.')
                record = {'id': secrets.token_hex(16), 'key': meta.key, 'owner_sub': owner,
                          'owner_username': request.session.get('username'),
                          'created_at': datetime.now(timezone.utc).isoformat(),
                          'corpus_version': CONFIG['latest'], 'scorer_version': __version__,
                          'corpus_fingerprint': corpus_fingerprint(corpus(CONFIG['latest'])),
                          'task_scorer_version': TASK_VERSION if meta.tasks[0] != 'alignment' else None,
                          'task_fingerprint': task_fingerprint(corpus(CONFIG['latest'])) if meta.tasks[0] != 'alignment' else None,
                          'metadata': meta.model_dump(mode='json'), 'replaces': proof['previous'],
                          'predictions': [s.model_dump(mode='json') for s in subs]}
                state['store'].put(record)
                state['records'].append(record)
                return {'published': True, 'id': record['id'], 'system': meta.system}
        return await run_in_threadpool(commit)

    async def parse_batch(files, metadata):
        try:
            metadata_errors = []
            try:
                meta = Metadata.model_validate_json(metadata)
            except ValidationError as exc:
                metadata_errors = [errors(exc)]
                supplied = json.loads(metadata)
                selected = supplied.get('tasks') if isinstance(supplied, dict) else None
                if not isinstance(selected, list) or not selected or any(t not in PRIMARY for t in selected):
                    raise ValueError('Select known tasks before uploading.') from None
                meta = Metadata(system='Preview', description='Private combined preview',
                                url='https://example.org', email='preview@example.org', user_parameters=0,
                                tasks=selected, hardware_class=supplied.get('hardware_class')
                                if supplied.get('hardware_class') in ('cpu', 'gpu') else None,
                                hardware=supplied.get('hardware') if isinstance(supplied.get('hardware'), str) else '')
            if len(set(meta.tasks)) != len(meta.tasks):
                raise ValueError('Select each task only once.')
            raw = []
            total = 0
            for file in files:
                data = await file.read(MAX_BYTES + 1)
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError('Uploads exceed 20 MB')
                raw.append((file.filename or '', data))
            grouped = {task: [] for task in meta.tasks}
            for name, data in unpack(raw):
                path = PurePosixPath(name.replace('\\', '/'))
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('Invalid prediction path')
                task = path.parts[0] if len(path.parts) > 1 else meta.tasks[0] if len(meta.tasks) == 1 else None
                if task not in grouped:
                    raise ValueError('Combined uploads need a selected task folder: alignment/, segmentation/, or timing/.')
                grouped[task].append((path.name, data))
            results, submissions = {}, {}
            for task in meta.tasks:
                single = meta.model_copy(update={'tasks': [task]})
                if not compute_lock.acquire(blocking=False):
                    raise HTTPException(429, 'Scoring is busy. Please retry shortly.')
                try:
                    subs, result = await run_in_threadpool(validate_files, grouped[task], corpus(CONFIG['latest']), single)
                finally:
                    compute_lock.release()
                old = current(meta.key, CONFIG['latest'], meta.hardware_class, task)
                result['errors'].extend(metadata_errors)
                if metadata_errors:
                    result['complete'] = False
                result['replacement'] = bool(old)
                result['previous'] = old['id'] if old else None
                results[task], submissions[task] = result, subs
            owners = {r['owner_sub'] for r in state['records'] if r['key'] == meta.key}
            return meta, submissions, results, owners
        except (ValueError, TypeError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, errors(exc)) from None

    def batch_proof(meta, submissions, results):
        return {'version': CONFIG['latest'], 'fingerprint': task_fingerprint(corpus(CONFIG['latest'])), 'tasks': {
            task: {'hash': digest(meta.model_copy(update={'tasks': [task]}), submissions[task]),
                   'previous': result['previous']} for task, result in results.items()}}

    def commit_batch(meta, submissions, proof, owner, username):
        with publish_lock:
            owners = {r['owner_sub'] for r in state['records'] if r['key'] == meta.key}
            if owners and owner not in owners:
                raise HTTPException(403, 'This system name belongs to another account')
            records = []
            for task in meta.tasks:
                old = current(meta.key, CONFIG['latest'], meta.hardware_class, task)
                if (old['id'] if old else None) != proof['tasks'][task]['previous']:
                    raise HTTPException(409, 'A selected task changed since preview. Review again.')
                records.append({'id': secrets.token_hex(16), 'key': meta.key, 'owner_sub': owner,
                    'owner_username': username,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'corpus_version': CONFIG['latest'],
                    'scorer_version': __version__, 'task_scorer_version': TASK_VERSION if task != 'alignment' else None,
                    'corpus_fingerprint': corpus_fingerprint(corpus(CONFIG['latest'])),
                    'task_fingerprint': task_fingerprint(corpus(CONFIG['latest'])) if task != 'alignment' else None,
                    'metadata': meta.model_copy(update={'tasks': [task]}).model_dump(mode='json'),
                    'replaces': proof['tasks'][task]['previous'],
                    'predictions': [s.model_dump(mode='json') for s in submissions[task]]})
            # One durable object is the atomic publication boundary for every selected task.
            state['store'].put({'id': secrets.token_hex(16), 'batch_records': records})
            state['records'].extend(records)
            return {'published': True, 'tasks': meta.tasks, 'ids': [r['id'] for r in records]}

    @app.post('/api/preview-batch')
    async def preview_batch(request: Request, files: Annotated[list[UploadFile], File()],
                            metadata: Annotated[str, Form()]):
        meta, submissions, results, owners = await parse_batch(files, metadata)
        complete = all(result['complete'] for result in results.values())
        return {'tasks': results, 'complete': complete,
                'owned_by_another': bool(owners and request.session.get('sub') not in owners),
                'token': signer.dumps(batch_proof(meta, submissions, results)) if complete else None}

    @app.post('/api/publish-batch')
    async def publish_batch(request: Request, files: Annotated[list[UploadFile], File()],
                            metadata: Annotated[str, Form()], preview_token: Annotated[str, Form()],
                            confirmed: Annotated[bool, Form()] = False):
        owner = request.session.get('sub')
        if not owner:
            raise HTTPException(401, 'Sign in with Hugging Face before publishing')
        if not confirmed:
            raise HTTPException(422, 'Review all selected tasks and confirm publication')
        meta, submissions, results, _ = await parse_batch(files, metadata)
        try:
            proof = signer.loads(preview_token, max_age=3600)
        except BadSignature:
            raise HTTPException(409, 'Preview expired. Review your results again.') from None
        if not all(r['complete'] for r in results.values()) or proof != batch_proof(meta, submissions, results):
            raise HTTPException(409, 'Files, tasks, or details changed. Preview all selected tasks again.')

        return await run_in_threadpool(commit_batch, meta, submissions, proof, owner,
                                       request.session.get('username'))

    @app.get('/api/v1/submissions/me', include_in_schema=False)
    async def submission_identity(request: Request):
        _, username = await bearer_identity(request)
        return {'authenticated': True, 'username': username}

    @app.post('/api/v1/submissions/preview', include_in_schema=False)
    async def submission_preview(request: Request, files: Annotated[list[UploadFile], File()],
                                 metadata: Annotated[str, Form()]):
        owner, username = await bearer_identity(request)
        meta, submissions, results, owners = await parse_batch(files, metadata)
        complete = all(result['complete'] for result in results.values())
        return {'account': {'username': username}, 'tasks': results, 'complete': complete,
                'owned_by_another': bool(owners and owner not in owners),
                'preview_token': signer.dumps(batch_proof(meta, submissions, results)) if complete else None}

    @app.post('/api/v1/submissions/publish', include_in_schema=False)
    async def submission_publish(request: Request, files: Annotated[list[UploadFile], File()],
                                 metadata: Annotated[str, Form()], preview_token: Annotated[str, Form()],
                                 confirmed: Annotated[bool, Form()] = False):
        owner, username = await bearer_identity(request)
        if not confirmed:
            raise HTTPException(422, 'Review all selected tasks and confirm publication')
        meta, submissions, results, _ = await parse_batch(files, metadata)
        try:
            proof = signer.loads(preview_token, max_age=3600)
        except BadSignature:
            raise HTTPException(409, 'Preview expired. Review your results again.') from None
        if not all(result['complete'] for result in results.values()) or proof != batch_proof(
                meta, submissions, results):
            raise HTTPException(409, 'Files, tasks, or details changed. Preview all selected tasks again.')
        return await run_in_threadpool(commit_batch, meta, submissions, proof, owner, username)

    static = ROOT / 'frontend' / 'dist'
    if static.exists():
        app.mount('/', StaticFiles(directory=static, html=True), name='frontend')
    return app


app = create_app()
