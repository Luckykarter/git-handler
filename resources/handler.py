import datetime
import pickle
import shutil

import git
import os
import time
import base64
from typing import Callable, Union, List, Optional
from enum import Enum
from resources.conf import settings
from fastapi import HTTPException, status
from socket import gethostname
from pydantic import BaseModel


class Phrase(BaseModel):
    content: str
    pre: Optional[str] = ''
    post: Optional[str] = ''


class FileCheck(BaseModel):
    filename: str
    phrases: List[Phrase]


class Locker:
    def __init__(self, locker_key='lock'):
        self.key = f'{locker_key}-{gethostname()}'
        self.ssh_key_path = ''

    def is_locked(self):
        if not os.path.isfile(self.key):
            return False
        with open(self.key, 'rb') as f:
            target_date = pickle.load(f)
        if target_date < datetime.datetime.now():
            os.remove(self.key)
            return False
        return True

    def wait_for_unlock(self):
        while self.is_locked():
            time.sleep(0.5)

    def lock(self):
        with open(self.key, 'wb') as f:
            pickle.dump(datetime.datetime.now() + datetime.timedelta(hours=1), f)

    def unlock(self):
        if self.is_locked():
            try:
                os.remove(self.key)
            except:
                pass

    def __enter__(self):
        self.wait_for_unlock()
        self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unlock()


class GitHandler:
    PATH = '/tmp/'
    TEST_PATH = os.path.join(PATH, 'test_repositories')
    DIRECTORY = 'repositories'
    CODEPAGE = 'utf-8'

    def __init__(self, repo: str, path: str = None,
                 default_branch: str = settings.DEFAULT_GIT_BRANCH,
                 refresh_time: datetime.timedelta = datetime.timedelta(minutes=10)):
        self.refresh_time = refresh_time
        self.hostname = gethostname()
        self.url = repo
        repo_path = repo.split('/')
        self.is_cloned = False
        repo_path = repo_path[3:]

        self._parent = os.path.join(
            path or self.PATH, self.DIRECTORY, repo_path.pop(0))
        self.target_dir = os.path.join(self._parent, *repo_path)
        self.default_branch = default_branch
        with Locker() as locker:
            try:
                self.repo = git.Repo(self.target_dir)
                self.update()
            except (git.InvalidGitRepositoryError, git.NoSuchPathError):
                if os.path.isdir(self.target_dir):  # pragma: no cover
                    shutil.rmtree(self.target_dir, onerror=onerror)
                os.makedirs(self.target_dir, mode=0o777)
                try:
                    self.repo = git.Repo.clone_from(repo, self.target_dir)
                    self.is_cloned = True
                except git.GitError as e:  # pragma: no cover
                    print(e)
                    shutil.rmtree(self.target_dir, onerror=onerror)
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail=f'Repository {repo} does not exist. Error: {e}')
            if default_branch != self.current_branch:
                try:
                    self.repo.git.checkout(default_branch)
                    self.update()
                except git.GitCommandError as e:  # pragma: no cover
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    class ContentProcessors(str, Enum):
        """
        definition of functions that are responsible for content processing
        """
        cp_add_blob_content = 'Add blob content'
        cp_encode_blob_base64 = "Encode blob to Base64"

    def update(self):
        self.repo.remote().fetch()
        self.repo.remote().pull()

    @property
    def current_branch(self):
        return self.repo.git.branch('--show-current')

    @property
    def update_key(self):
        return f'updated-{self.url}-{self.hostname}'

    def is_update_required(self):
        return True

    def get_remote_branches(self) -> List[git.RemoteReference]:
        return self.repo.remote().refs

    def get_branches_names(self) -> List[str]:
        return [x.name.replace('origin/', '') for x in self.get_remote_branches()]

    def get_params_from_cfg(self, content: bytes) -> dict:
        params = {}
        params_started = False
        for x in content.decode(self.CODEPAGE).split('\n'):
            if 'config_params' in x:
                params_started = True
                continue
            if params_started and '}' in x:
                break
            x = x.strip()
            if x.startswith('#') or x.startswith(';'):  # pragma: no cover
                continue
            line = x.split(' ', 1)
            if len(line) == 1:
                params[line[0]] = True  # pragma: no cover
            elif len(line) == 2:
                params[line[0]] = line[1].replace('\"', '')
        return params

    def get_tree(self, branch: str = None,
                 path: Union[str, list] = None,
                 content_processors: List[Callable[[dict, dict, git.Blob], None]] = None):
        if path is None:
            path = ['']
        elif isinstance(path, str):
            path = path.split('/')
        if isinstance(path, list) and path[0] != '':
            path.insert(0, '')

        branch = branch or self.default_branch
        branch = self.repo.remote().refs[branch]
        res = {}
        self._get_tree(res, self.repo.tree(branch), path,
                       content_processors=content_processors)
        return res

    def files_contains(self, checks: List[FileCheck]):
        self.update()
        for file_check in checks:
            filename = file_check.filename
            current_file = (f'<a href="{self.url}/blob/{self.default_branch}/{filename}" '
                            f'class="font-weight-bold" target="_blank"><u>{filename}</u></a>')
            file_content = self.get_file(filename)
            for phrase in file_check.phrases:
                content = phrase.content.strip()
                lines = content.split('\n')
                for line in lines:
                    if line.strip() not in file_content:
                        msg = (f'Файл {current_file} должен содержать '
                               f'{phrase.pre}<pre><code>{content}</code></pre>')
                        if phrase.post:
                            msg += '\n' + phrase.post
                        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)

    def get_file(self, filename: str) -> str:
        full_path = os.path.join(self.target_dir, filename)
        if not os.path.isfile(full_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f'Файл {filename} не найден в репозитории '
                f'<a href="{self.url}" class="font-weight-bold" target="_blank"><u>{self.url}</u></a>')

        with open(os.path.join(self.target_dir, full_path), 'r') as f:
            return f.read()

    def cp_add_blob_content(self, node: dict, file_dict: dict, blob: git.Blob):
        content = blob.data_stream.read()
        try:
            content = content.decode(self.CODEPAGE)
        except UnicodeDecodeError:  # pragma: no cover
            content = str(content)
        file_dict['content'] = content

    def cp_encode_blob_base64(self, node: dict, file_dict: dict, blob: git.Blob):
        content = file_dict.get('content')
        if not content:  # pragma: no cover
            self.cp_add_blob_content(node, file_dict, blob)
        content = file_dict['content']
        file_dict['content'] = base64.b64encode(content.encode(self.CODEPAGE))

    def _get_tree(self, res: dict, tree: git.Tree, path: list,
                  idx=0, content_processors: List[Callable[[dict, dict, git.Blob], None]] = None):
        if idx < len(path):
            node = path[idx]
            if tree.name.lower() != node.lower():
                return

        if tree.name not in res:
            res[tree.name] = {}
        if tree.blobs:
            res[tree.name] = {'files': []}
            for b in tree.blobs:
                file_dict = {'filename': b.name}
                if content_processors is not None:
                    for func in content_processors:
                        func(res[tree.name], file_dict, b)
                res[tree.name]['files'].append(file_dict)
        if tree.trees:
            res = res[tree.name]
            for t in tree.trees:
                self._get_tree(res, t, path, idx + 1,
                               content_processors=content_processors)


def check_git_login(repository: str, github_token: str):
    return True
    # redis_key = f'login-{repository}-{github_token}'
    # if rds.exists(redis_key):
    #     return
    #
    # gh = Github(github_token)
    # try:
    #     gh.get_repo(repository)
    #     rds.set(redis_key, 'x')
    #     rds.expire(redis_key, datetime.timedelta(hours=24))
    # except GithubException as e:
    #     raise HTTPException(e.status, detail=e.data.get('message'))


def onerror(func, path, exc_info):  # pragma: no cover
    """
    Error handler for ``shutil.rmtree``.

    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.

    If the error is for another reason it re-raises the error.

    Usage : ``shutil.rmtree(path, onerror=onerror)``
    """
    import stat
    # Is the error an access error?
    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise
