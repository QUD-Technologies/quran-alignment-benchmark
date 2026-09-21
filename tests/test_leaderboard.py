"""Publication, privacy, and arbitrary-subset parity at the HTTP boundary."""
import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from leaderboard.app import Metadata, create_app
from leaderboard.storage import Store
from qab.report import evaluate
from qab.schema import Case, Submission


@pytest.fixture
def cases():
    return [Case(id='one', duration_s=6, style='hadr', content='quran_only', noisy=False, multi_surah=False,
                 words=[{'word': f'2:1:{i}', 'start_s': j, 'end_s': j + .8}
                        for j, i in enumerate([1, 1])]),
            Case(id='two', duration_s=8, style='murattal', content='prayer', noisy=True, multi_surah=False,
                 words=[{'word': '2:1:1', 'start_s': 1, 'end_s': 2}])]


@pytest.fixture
def metadata():
    return {'system': 'Example System', 'description': 'A documented public system.', 'url': 'https://example.org',
            'email': 'secret@example.org', 'user_parameters': 2, 'hardware_class': 'cpu',
            'hardware': 'Test CPU', 'tasks': ['alignment']}


def predictions(cases):
    return [Submission(case_id=c.id, runtime_seconds=n + 1,
                       segments=[{'start_s': w.start_s, 'end_s': w.end_s,
                                  'reference': f'{w.word}-{w.word}', 'confidence': .8 + .1 * n}
                                 for w in c.words]) for n, c in enumerate(cases)]


def payload(subs):
    return [('files', (s.case_id + '.json', s.model_dump_json(), 'application/json')) for s in subs]


def login(client, subject='owner'):
    client.cookies.clear()
    raw = base64.b64encode(json.dumps({'sub': subject, 'username': 'private-hf-name'}).encode())
    client.cookies.set('session', TimestampSigner('test-secret').sign(raw).decode())


@pytest.fixture
def client(tmp_path, monkeypatch, cases):
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    with TestClient(create_app(cases, Store(str(tmp_path)))) as client:
        client.headers['x-qab-request'] = '1'
        yield client


def preview(client, meta, subs):
    return client.post('/api/preview', data={'metadata': json.dumps(meta)}, files=payload(subs))


def publish(client, meta, subs, token):
    return client.post('/api/publish', data={'metadata': json.dumps(meta), 'preview_token': token,
                                           'confirmed': 'true'}, files=payload(subs))


def test_partial_preview_and_invalid_metadata_still_validate_files(client, metadata, cases):
    subs = predictions(cases)
    p = preview(client, metadata, subs[:1]).json()
    assert p['valid'] == 1 and p['scores'] and not p['complete'] and p['token'] is None
    metadata['email'] = ''
    p = preview(client, metadata, subs).json()
    assert p['valid'] == 2 and p['errors'] and not p['complete']


def test_publish_auth_privacy_replacement_and_subset_parity(client, metadata, cases):
    subs = predictions(cases)
    p = preview(client, metadata, subs).json()
    assert p['complete']
    assert publish(client, metadata, subs, p['token']).status_code == 401
    login(client)
    first = publish(client, metadata, subs, p['token'])
    assert first.status_code == 200, first.text
    for ids in [['one'], ['two'], ['one', 'two']]:
        result = client.get('/api/leaderboard', params=[('ids', i) for i in ids])
        assert result.status_code == 200
        row = result.json()['rows'][0]
        assert row['scorer_version'] == '0.2.1'
        direct = evaluate([c for c in cases if c.id in ids], [s for s in subs if s.case_id in ids],
                          Metadata(**metadata).scorer_meta())
        assert row['scores'] == direct['pooled']
        assert row['scores']['trusted_coverage'] is not None
        assert row['scores']['unsafe_green'] is not None
        for private in ['secret@example.org', 'private-hf-name', 'owner_sub', 'predictions', '"segments":']:
            assert private not in result.text
    assert publish(client, metadata, subs, p['token']).status_code == 409
    subs[0].segments = []
    p2 = preview(client, metadata, subs).json()
    assert p2['replacement']
    second = publish(client, metadata, subs, p2['token'])
    assert second.status_code == 200, second.text
    assert len(client.get('/api/leaderboard').json()['rows']) == 1
    login(client, 'another-owner')
    p3 = preview(client, metadata, subs).json()
    assert p3['owned_by_another']
    assert publish(client, metadata, subs, p3['token']).status_code == 403


def test_changed_preview_rejected(client, metadata, cases):
    subs = predictions(cases)
    p = preview(client, metadata, subs).json()
    login(client)
    metadata['description'] = 'Changed after viewing scores'
    assert publish(client, metadata, subs, p['token']).status_code == 409


def test_invalid_files_duplicates_unknown_zip_and_consistency(client, metadata, cases):
    subs = predictions(cases)
    subs[0].runtime_seconds = None
    assert not preview(client, metadata, subs).json()['complete']
    subs[0].segments[0].end_s = 100
    p = preview(client, metadata, subs).json()
    assert p['recordings'][0]['status'] == 'invalid'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('../one.json', '{"segments":[]}')
        z.writestr('missing.json', '{"segments":[]}')
    p = client.post('/api/preview', data={'metadata': json.dumps(metadata)},
                    files={'files': ('a.zip', buf.getvalue())}).json()
    assert len(p['file_errors']) == 2
    clean = predictions(cases)
    p = client.post('/api/preview', data={'metadata': json.dumps(metadata)},
                    files=payload(clean) + payload(clean[:1])).json()
    assert p['recordings'][0]['status'] == 'invalid'
    assert not p['complete']


def test_store_rebuild_retains_history(tmp_path, monkeypatch, metadata, cases):
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    store = Store(str(tmp_path))
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        login(client)
        subs = predictions(cases)
        for n in range(2):
            metadata['description'] = f'A public system, submission {n}.'
            token = preview(client, metadata, subs).json()['token']
            assert publish(client, metadata, subs, token).status_code == 200
    assert len(store.records()) == 2
    with TestClient(create_app(cases, store)) as client:
        rows = client.get('/api/leaderboard').json()['rows']
        assert len(rows) == 1 and rows[0]['description'].endswith('1.')


def test_public_corpus_has_no_audio_or_truth(client):
    result = client.get('/api/corpus')
    assert result.status_code == 200
    row = result.json()['recordings'][0]
    assert 'audio' not in row and 'words' not in row and 'truth' not in row
    assert row['repeats'] == 1
    assert client.get('/api/corpus?version=v0').status_code == 404
    assert client.get('/api/leaderboard?ids=unknown').status_code == 422


def test_dataset_repeat_count_uses_word_ordinals_not_chapter_numbers(tmp_path):
    case = Case(id='forward', duration_s=8, style='hadr', content='quran_only', noisy=False,
                multi_surah=False, words=[{'word': word, 'start_s': i, 'end_s': i + .8}
                                         for i, word in enumerate(['2:2:1', '2:2:2', '2:2:3', '2:2:1'])])
    with TestClient(create_app([case], Store(str(tmp_path)))) as client:
        assert client.get('/api/corpus').json()['recordings'][0]['repeats'] == 1


@pytest.mark.parametrize(('reference', 'guidance'), [
    ('2:5:1', 'Use "2:5:1-2:5:1" instead'),
    ('84:1', 'Include word numbers at both ends'),
    ('2:2:3-2:2:1', 'Represent a repeat with a new timed segment'),
    ('2:2:1-3:1:1', 'separate timed segment for each surah'),
    ('999:1:1-999:1:1', '1-based word numbers against the Hafs dataset'),
])
def test_reference_errors_are_actionable(client, metadata, reference, guidance):
    response = client.post('/api/preview', data={'metadata': json.dumps(metadata)}, files={
        'files': ('one.json', json.dumps({'segments': [{'start_s': 0, 'end_s': 1, 'reference': reference}]}))})
    assert response.status_code == 200
    issue = response.json()['recordings'][0]['error']
    assert 'Segment 1, reference:' in issue and guidance in issue


def test_malformed_json_has_line_and_column(client, metadata):
    response = client.post('/api/preview', data={'metadata': json.dumps(metadata)},
                           files={'files': ('one.json', '{"segments": [}')})
    issue = response.json()['recordings'][0]['error']
    assert 'line 1, column' in issue and 'Fix the JSON' in issue


def test_cpu_gpu_profiles_replace_independently_and_share_ownership(client, metadata, cases):
    login(client)
    subs = predictions(cases)
    ids = {}
    for profile in ['cpu', 'gpu']:
        metadata['hardware_class'] = profile
        p = preview(client, metadata, subs).json()
        assert not p['replacement']
        result = publish(client, metadata, subs, p['token'])
        assert result.status_code == 200, result.text
        ids[profile] = result.json()['id']
    rows = client.get('/api/leaderboard').json()['rows']
    assert {r['display_name'] for r in rows} == {'Example System (CPU)', 'Example System (GPU)'}
    assert all(r['task'] == 'alignment' and r['corpus_version'] == 'v1' for r in rows)
    assert all(r['recording_count'] == len(cases) and r['scorer_version'] for r in rows)
    assert all('email' not in r and 'predictions' not in r for r in rows)
    metadata['hardware_class'] = 'cpu'
    p = preview(client, metadata, subs).json()
    assert p['replacement']
    assert publish(client, metadata, subs, p['token']).status_code == 200
    rows = client.get('/api/leaderboard').json()['rows']
    assert len(rows) == 2
    assert next(r for r in rows if r['hardware_class'] == 'gpu')['id'] == ids['gpu']
    assert next(r for r in rows if r['hardware_class'] == 'cpu')['id'] != ids['cpu']
    login(client, 'another-owner')
    metadata['hardware_class'] = 'gpu'
    p = preview(client, metadata, subs).json()
    assert publish(client, metadata, subs, p['token']).status_code == 403

def test_sign_in_draft_handoff_is_private_and_expires(client, monkeypatch):
    draft = {'metadata': {'system': '', 'email': 'private@example.org'}, 'profiles': {
        'cpu': {'hardware': '', 'files': [{'name': 'one.json', 'text': '{invalid json'}]},
        'gpu': {'hardware': 'GPU', 'files': [{'name': 'two.json', 'text': '{"segments": []}'}]},
    }, 'corpus': 'v1'}
    result = client.post('/api/draft-handoff', json=draft)
    assert result.status_code == 200
    token = result.json()['token']
    assert client.post('/api/draft-resume', json={'token': token}).json() == draft
    assert client.post('/api/draft-resume', json={'token': 'x' * 43}).status_code == 410
    assert client.post('/api/draft-resume', json={'token': []}).status_code == 400
    assert 'private@example.org' not in client.get('/api/leaderboard').text
    assert client.get('/api/session').json()['signed_in'] is False
    import time
    now = time.time()
    monkeypatch.setattr('leaderboard.drafts.time.time', lambda: now + 3601)
    assert client.post('/api/draft-resume', json={'token': token}).status_code == 410


@pytest.mark.parametrize('cancelled', [False, True])
def test_oauth_returns_to_saved_draft(tmp_path, monkeypatch, cases, cancelled):
    from fastapi.responses import RedirectResponse

    class FakeHF:
        async def authorize_redirect(self, request, redirect):
            return RedirectResponse('/fake-hf-consent')

        async def authorize_access_token(self, request):
            if cancelled:
                raise ValueError('Consent cancelled')
            return {'userinfo': {'sub': 'owner', 'preferred_username': 'private-user'}}

    class FakeOAuth:
        hf = FakeHF()

        def register(self, *args, **kwargs):
            pass

    monkeypatch.setattr('leaderboard.app.OAuth', FakeOAuth)
    monkeypatch.setenv('OAUTH_CLIENT_ID', 'test')
    monkeypatch.setenv('OAUTH_CLIENT_SECRET', 'test')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    with TestClient(create_app(cases, Store(str(tmp_path)))) as client:
        token = client.post('/api/draft-handoff', json={'metadata': {}, 'profiles': {}},
                            headers={'x-qab-request': '1'}).json()['token']
        assert client.get('/auth/login', params={'resume': token}, follow_redirects=False).status_code == 307
        response = client.get('/auth/callback', follow_redirects=False)
        assert response.headers['location'] == '/#submit?resume=' + token + ('&auth_error=1' if cancelled else '')
        assert client.get('/api/session').json()['signed_in'] is not cancelled
        assert client.post('/api/draft-resume', json={'token': token},
                           headers={'x-qab-request': '1'}).json() == {'metadata': {}, 'profiles': {}}


@pytest.mark.parametrize('task', ['segmentation', 'timing'])
def test_parallel_task_publication_and_replacement(tmp_path, monkeypatch, cases, metadata, task):
    from qab.schema import ReviewedSegment
    from qab.tasks import MODELS, evaluate_task
    for c in cases:
        c.segments = [ReviewedSegment(start_s=w.start_s, end_s=w.end_s,
                                      first_word=w.word, last_word=w.word) for w in c.words]
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    store = Store(str(tmp_path))
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        login(client)
        alignment = predictions(cases)
        proof = preview(client, metadata, alignment).json()
        assert publish(client, metadata, alignment, proof['token']).status_code == 200
        alignment_id = client.get('/api/leaderboard').json()['rows'][0]['id']
        metadata['tasks'] = [task]
        subs = [MODELS[task].model_validate({'case_id': c.id, **(
            {'segments': [{'start_s': w.start_s, 'end_s': w.end_s} for w in c.words]}
            if task == 'segmentation' else {'clips': [{'words': None} for _ in c.segments]})}) for c in cases]
        proof = preview(client, metadata, subs).json()
        assert proof['complete'] and not proof['replacement'], proof
        response = publish(client, metadata, subs, proof['token'])
        assert response.status_code == 200, response.text
        first_id = response.json()['id']
        for ids in [['one'], ['two'], ['one', 'two']]:
            board = client.get('/api/leaderboard', params=[('task', task)] + [('ids', i) for i in ids]).json()
            expected = evaluate_task(task, [c for c in cases if c.id in ids],
                                     [s for s in subs if s.case_id in ids], metadata and Metadata(**metadata).scorer_meta())
            assert board['rows'][0]['scores'] == expected['pooled']
            assert 'private-hf-name' not in json.dumps(board)
            assert 'secret@example.org' not in json.dumps(board)
            assert 'predictions' not in json.dumps(board)
        proof = preview(client, metadata, subs).json()
        assert proof['replacement']
        assert publish(client, metadata, subs, proof['token']).status_code == 200
        records = store.records()
        assert len(records) == 3 and any(r['replaces'] == first_id for r in records)
        assert client.get('/api/leaderboard').json()['rows'][0]['id'] == alignment_id
        metadata['hardware_class'] = 'gpu'
        proof = preview(client, metadata, subs).json()
        assert not proof['replacement']
        assert publish(client, metadata, subs, proof['token']).status_code == 200
        assert len(client.get('/api/leaderboard', params={'task': task}).json()['rows']) == 2
        # A preview is tied to its task as well as files and metadata.
        metadata['tasks'] = ['alignment']
        assert publish(client, metadata, alignment, proof['token']).status_code == 409


def batch_payload(cases):
    from qab.schema import ReviewedSegment
    for c in cases:
        c.segments = [ReviewedSegment(start_s=w.start_s, end_s=w.end_s,
                                     first_word=w.word, last_word=w.word) for w in c.words]
    result = payload(predictions(cases))
    result = [('files', ('alignment/' + entry[1][0], *entry[1][1:])) for entry in result]
    for c in cases:
        result += [('files', ('segmentation/' + c.id + '.json', json.dumps({'segments': [
            {'start_s': w.start_s, 'end_s': w.end_s} for w in c.words]}), 'application/json')),
                   ('files', ('timing/' + c.id + '.json', json.dumps({'clips': [
                       {'words': None} for _ in c.segments]}), 'application/json'))]
    return result


def test_batch_publishes_all_tasks_atomically_and_restores(tmp_path, monkeypatch, cases, metadata):
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    files = batch_payload(cases)
    metadata['tasks'] = ['alignment', 'segmentation', 'timing']
    data = {'metadata': json.dumps(metadata)}
    store = Store(str(tmp_path))
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        login(client)
        preview = client.post('/api/preview-batch', data=data, files=files).json()
        assert preview['complete'] and set(preview['tasks']) == set(metadata['tasks'])
        incomplete = client.post('/api/preview-batch', data=data, files=files[:-1]).json()
        assert not incomplete['complete'] and incomplete['token'] is None
        assert incomplete['tasks']['alignment']['complete']
        assert incomplete['tasks']['segmentation']['complete']
        request = {**data, 'preview_token': preview['token'], 'confirmed': 'true'}
        assert client.post('/api/publish-batch', data=request, files=files[:-1]).status_code == 409
        assert store.records() == []
        response = client.post('/api/publish-batch', data=request, files=files)
        assert response.status_code == 200, response.text
        assert set(response.json()['tasks']) == set(metadata['tasks'])
        assert len(list(tmp_path.glob('*.json'))) == 1  # one atomic durable envelope
        assert len(store.records()) == 3
        assert client.post('/api/publish-batch', data=request, files=files).status_code == 409
        # Replacing a selected subset leaves the third task unchanged.
        timing_id = client.get('/api/leaderboard?task=timing').json()['rows'][0]['id']
        metadata['tasks'] = ['alignment', 'segmentation']
        subset = [f for f in files if not f[1][0].startswith('timing/')]
        data = {'metadata': json.dumps(metadata)}
        proof = client.post('/api/preview-batch', data=data, files=subset).json()
        assert all(r['replacement'] for r in proof['tasks'].values())
        assert client.post('/api/publish-batch', data={**data, 'preview_token': proof['token'],
                           'confirmed': 'true'}, files=subset).status_code == 200
        assert client.get('/api/leaderboard?task=timing').json()['rows'][0]['id'] == timing_id
    with TestClient(create_app(cases, store)) as restored:
        for task in ['alignment', 'segmentation', 'timing']:
            assert len(restored.get('/api/leaderboard', params={'task': task}).json()['rows']) == 1
    before = len(list(tmp_path.glob('*.json')))
    store.put({'id': 'clear-board', 'hidden_record_ids': [r['id'] for r in store.records()]})
    assert store.records() == []
    assert len(list(tmp_path.glob('*.json'))) == before + 1  # retained, not deleted


def test_batch_persistence_failure_does_not_publish_partial_results(tmp_path, monkeypatch, cases, metadata):
    files = batch_payload(cases)
    metadata['tasks'] = ['alignment', 'segmentation', 'timing']
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    store = Store(str(tmp_path))
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        login(client)
        data = {'metadata': json.dumps(metadata)}
        preview = client.post('/api/preview-batch', data=data, files=files).json()
        def fail(record):
            raise OSError('Storage unavailable')
        monkeypatch.setattr(store, 'put', fail)
        with pytest.raises(OSError, match='Storage unavailable'):
            client.post('/api/publish-batch', files=files, data={**data,
                        'preview_token': preview['token'], 'confirmed': 'true'})
        for task in metadata['tasks']:
            assert client.get('/api/leaderboard', params={'task': task}).json()['rows'] == []


def test_token_submission_api_auth_preview_publish_and_privacy(tmp_path, monkeypatch, cases, metadata):
    store = Store(str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    identity = [('hf-owner-id', 'private-hf-name')]
    monkeypatch.setattr('leaderboard.app.verify_hf_token', lambda token: identity[0])
    files = payload(predictions(cases))
    data = {'metadata': json.dumps(metadata)}
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        assert client.get('/api/v1/submissions/me').status_code == 401
        headers = {'Authorization': 'Bearer hf_private_test_token'}
        assert client.get('/api/v1/submissions/me', headers=headers).json() == {
            'authenticated': True, 'username': 'private-hf-name'}
        result = client.post('/api/v1/submissions/preview', headers=headers, data=data, files=files)
        assert result.status_code == 200, result.text
        preview = result.json()
        assert preview['complete'] and set(preview['tasks']) == {'alignment'}
        assert preview['account'] == {'username': 'private-hf-name'}
        assert preview['preview_token'] and 'hf_private_test_token' not in result.text
        publish_data = {**data, 'preview_token': preview['preview_token'], 'confirmed': 'true'}
        result = client.post('/api/v1/submissions/publish', headers=headers, data=publish_data, files=files)
        assert result.status_code == 200, result.text
        assert result.json()['tasks'] == ['alignment']
        assert 'hf_private_test_token' not in json.dumps(store.records())
        identity[0] = ('another-hf-id', 'another-private-name')
        preview = client.post('/api/v1/submissions/preview', headers=headers, data=data, files=files).json()
        assert preview['owned_by_another']
        result = client.post('/api/v1/submissions/publish', headers=headers,
                             data={**data, 'preview_token': preview['preview_token'], 'confirmed': 'true'},
                             files=files)
        assert result.status_code == 403
        assert client.get('/api/openapi.json').status_code == 404
        assert client.get('/api/docs').status_code == 404
        assert client.get('/api/redoc').status_code == 404


def test_token_submission_api_accepts_all_task_subsets(tmp_path, monkeypatch, cases, metadata):
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    monkeypatch.delenv('SPACE_HOST', raising=False)
    monkeypatch.setattr('leaderboard.app.verify_hf_token', lambda token: ('hf-owner-id', 'private-hf-name'))
    metadata['tasks'] = ['alignment', 'segmentation', 'timing']
    files = batch_payload(cases)
    data = {'metadata': json.dumps(metadata)}
    headers = {'Authorization': 'Bearer hf_private_test_token'}
    store = Store(str(tmp_path))
    with TestClient(create_app(cases, store)) as client:
        client.headers['x-qab-request'] = '1'
        preview = client.post('/api/v1/submissions/preview', headers=headers, data=data, files=files).json()
        assert preview['complete'] and set(preview['tasks']) == set(metadata['tasks'])
        result = client.post('/api/v1/submissions/publish', headers=headers,
                             data={**data, 'preview_token': preview['preview_token'], 'confirmed': 'true'},
                             files=files)
        assert result.status_code == 200, result.text
        assert set(result.json()['tasks']) == set(metadata['tasks'])
        assert len(store.records()) == 3


def test_selected_version_refreshes_all_dataset_fields(tmp_path, monkeypatch, cases):
    import importlib
    module = importlib.import_module('leaderboard.app')
    clock = [0.0]
    loads = []
    monkeypatch.delenv('QAB_CASES_DIR', raising=False)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    def load(version, cases_dir=None):
        loads.append(version)
        return cases, [{'id': c.id, 'description': 'Latest description ' + str(len(loads)),
                        'passages': 'Current dataset passages'} for c in cases]
    monkeypatch.setattr(module, 'load_bundle', load)
    with TestClient(create_app(store_override=Store(str(tmp_path)))) as client:
        first = client.get('/api/corpus').json()
        assert first['recordings'][0]['description'] == 'Latest description 1'
        assert 'revision' not in first
        clock[0] = 61
        second = client.get('/api/corpus').json()
        assert second['recordings'][0]['description'] == 'Latest description 2'
        assert second['recordings'][0]['passages'] == 'Current dataset passages'
        assert loads == ['v1', 'v1']
