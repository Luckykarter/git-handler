import datetime
import os
import pickle
import time
from typing import List

import pytest
from fastapi.testclient import TestClient
from main import app
from resources.conf import settings
from resources.handler import GitHandler, onerror, Locker, FileCheck, Phrase
import shutil

REPO = 'https://github.com/LuckyKarter/git-handler'
PATHS = [None, 'resources']
BRANCHES = ['main', 'test']
DEFAULT_BRANCH = 'main'


@pytest.fixture(scope='session')
def repos_directory():
    return os.path.join(os.path.abspath(__file__), GitHandler.DIRECTORY)


@pytest.fixture(scope='session')
def headers():
    return {'Authorization': 'test-token'}


@pytest.fixture(scope='session', autouse=True)
def test_directory():
    def __remove(path):
        if os.path.isdir(path):
            shutil.rmtree(path, onerror=onerror)

    test_dir = GitHandler.TEST_PATH
    __remove(test_dir)
    __remove(os.path.join(GitHandler.PATH, GitHandler.DIRECTORY))
    yield test_dir
    __remove(test_dir)
    __remove(os.path.join(GitHandler.PATH, GitHandler.DIRECTORY))


def test_main():
    with TestClient(app) as client:
        response = client.get('/')
        assert response.status_code == 200


def test_healthcheck():
    with TestClient(app) as client:
        response = client.get('/healthcheck')
        assert response.status_code == 200


def test_update_repo(test_directory, headers):
    with TestClient(app) as client:
        for _ in range(2):  # first clone, second - update
            gh = GitHandler(REPO)
            res = client.get(
                f'/update/{REPO}', params={'branch': DEFAULT_BRANCH}, headers=headers)
            assert res.status_code == 200
            detail = res.json().get('detail')
            assert REPO in detail


def test_no_token():
    with TestClient(app) as client:
        response = client.get(f'/update/{REPO}')
        assert response.status_code == 403


def test_branch_not_exists(headers):
    with TestClient(app) as client:
        response = client.get(
            f'/tree/{REPO}/',
            params={"branch": 'non-existing-branch'},
            headers=headers
        )
        assert response.status_code == 400
        d = response.json()
        assert d['detail']


def test_get_branches(headers):
    with TestClient(app) as client:
        response = client.get(
            f'/branches/{REPO}', params={'branch': DEFAULT_BRANCH}, headers=headers)
        assert response.status_code == 200
        branches = response.json()
        assert isinstance(branches, list)
        assert len(branches) > 0


@pytest.mark.parametrize('filename,status_code', (
        ('main.py', 200),
        ('tests/NOT_EXIST.cfg', 400),
))
def test_get_file(headers, filename, status_code):
    with TestClient(app) as client:
        response = client.get(f'/file/{REPO}',
                              params={
                                  'branch': DEFAULT_BRANCH,
                                  'filename': filename,
                                  'force_update': True
                              },
                              headers=headers)
        assert response.status_code == status_code


@pytest.mark.parametrize("contents,status_code", (
        (['healthcheck', 'import'], 200),
        (['not_exist'], 400)
))
def test_file_contains(headers, contents: List[str], status_code: int):
    file_check = FileCheck(filename='main.py', phrases=[Phrase(content=x, post='test') for x in contents])
    with TestClient(app) as client:
        response = client.post(f'/file/contains/{REPO}',
                               params={
                                   'branch': DEFAULT_BRANCH,
                                   'force_update': True
                               },
                               json=[file_check.model_dump()],
                               headers=headers)
        print(response.json())
        assert response.status_code == status_code


@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize("path", PATHS)
def test_get_tree(branch, path, headers):
    content_processors = [x.value for x in GitHandler.ContentProcessors]
    with TestClient(app) as client:
        response = client.get(
            f'/tree/{REPO}/',
            params={
                "path": path,
                "branch": branch,
                "content_processors": content_processors
            },
            headers=headers
        )
        assert response.status_code == 200
        tree = response.json()
        p = tree.get("")
        if path:
            tokens = path.split('/')
            for token in tokens:
                assert token in p
                p = p[token]
        else:
            assert "main.py" in [x.get("filename") for x in p.get("files")]


def test_locker():
    locker = Locker()
    assert not locker.is_locked()
    with Locker() as locker:
        assert locker.is_locked()
        assert not Locker('test').is_locked()
    assert not locker.is_locked()


def test_wait_lock():
    locker = Locker()
    with open(locker.key, 'wb') as f:
        pickle.dump(datetime.datetime.now() + datetime.timedelta(seconds=2), f)
    assert locker.is_locked()
    locker.wait_for_unlock()
    assert not locker.is_locked()
