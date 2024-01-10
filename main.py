import os
import traceback
from fastapi import FastAPI, Request, Query, Response, status, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from resources.handler import GitHandler, check_git_login, Phrase, FileCheck
from resources.conf import settings
from functools import wraps
from typing import List, Optional

description = """
"""

app = FastAPI(
    title="Git Handler",
    description=description,
    version='1.0.0'
)


class GitHubToken(APIKeyHeader):
    pass


github_token_header = GitHubToken(name='Authorization')


class ResponseModel(BaseModel):
    success: bool = True
    detail: str


class FileResponseModel(BaseModel):
    filename: str
    content: str


class ExceptionModel(ResponseModel):
    success: bool = False
    traceback: list = []


class HealthCheckResponse(BaseModel):
    client_ip: str
    method: str
    headers: dict
    query_params: dict
    message: str = "Response from Git Proxy"


async def get_response(model: BaseModel, status_code: int):  # pragma: no cover
    return JSONResponse(jsonable_encoder(model),
                        status_code=status_code)


async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:  # pragma: no cover
        # TODO: send an e-mail with trace in production
        trc = traceback.format_exc()
        print(trc)
        return await get_response(ExceptionModel(detail=str(e), traceback=trc.split('\n')),
                                  status.HTTP_500_INTERNAL_SERVER_ERROR)


def git_login(f):
    @wraps(f)
    async def wrap(request: Request, git_path: str, *args, token: Optional[str] = None, **kwargs):
        check_git_login(git_path, token)
        return await f(request, git_path, *args, **kwargs)

    return wrap


app.middleware('http')(catch_exceptions_middleware)


def is_test_mode(request: Request):
    return request.headers.get('host') == 'testserver'


def get_git_handler(request: Request, git_path: str, default_branch: str = settings.DEFAULT_GIT_BRANCH,
                    force_update: bool = False):
    path = None
    if is_test_mode(request):
        path = GitHandler.TEST_PATH
    res = GitHandler(git_path, path, default_branch)
    if force_update:
        res.update()
    return res


@app.get('/update/{git_path:path}/', response_model=ResponseModel)
@git_login
async def update_repo(request: Request,
                      git_path: str,
                      token=Security(github_token_header),
                      force_update: Optional[bool] = False,
                      branch: Optional[str] = settings.DEFAULT_GIT_BRANCH) -> ResponseModel:
    """
    Clones repository if does not exist - otherwise - updates from remote
    """
    gh = get_git_handler(request, git_path, branch)
    if gh.is_cloned:
        detail = f'{git_path} cloned'
    elif force_update or gh.is_update_required():
        gh.update()
        detail = f'{git_path} updated'
    else:
        detail = f'{git_path} does not require update'
    return ResponseModel(detail=detail)


@app.get('/branches/{git_path:path}/', response_model=List[str])
@git_login
async def get_branches(request: Request,
                       git_path: str,
                       branch: Optional[str] = settings.DEFAULT_GIT_BRANCH,
                       force_update: Optional[bool] = False,
                       token=Security(github_token_header)):
    gh = get_git_handler(request, git_path, branch, force_update)
    return gh.get_branches_names()


@app.get('/tree/{git_path:path}/', response_model=dict)
@git_login
async def get_tree(request: Request,
                   git_path: str,
                   path: Optional[str] = None,
                   branch: Optional[str] = settings.DEFAULT_GIT_BRANCH,
                   token=Security(github_token_header),
                   force_update: Optional[bool] = False,
                   content_processors: Optional[List[GitHandler.ContentProcessors]] = Query([])):
    gh = get_git_handler(request, git_path, branch, force_update)
    for i, cp in enumerate(content_processors):
        content_processors[i] = getattr(gh, cp.name)
    try:
        tree = gh.get_tree(branch, path, content_processors)
    except IndexError:  # pragma: no cover
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Branch {branch} does not exist")
    return tree


@app.get('/file/{git_path:path}/', response_model=FileResponseModel)
@git_login
async def get_file(request: Request,
                   git_path: str,
                   filename: str,
                   branch: Optional[str] = settings.DEFAULT_GIT_BRANCH,
                   force_update: Optional[bool] = False,
                   token=Security(github_token_header)) -> FileResponseModel:
    gh = get_git_handler(request, git_path, branch, force_update)
    content = gh.get_file(filename)
    return FileResponseModel(filename=filename, content=content)


@app.post('/file/contains/{git_path:path}/')
@git_login
async def get_file(request: Request,
                   git_path: str,
                   phrases: List[FileCheck],
                   branch: Optional[str] = settings.DEFAULT_GIT_BRANCH,
                   force_update: Optional[bool] = False,
                   token=Security(github_token_header)) -> dict:
    gh = get_git_handler(request, git_path, branch, force_update)
    gh.files_contains(phrases)
    return {'detail': ''}


@app.get('/healthcheck/', response_model=HealthCheckResponse)
def healthcheck(request: Request) -> HealthCheckResponse:
    return HealthCheckResponse(
        client_ip=request.client.host,
        method=request.method,
        headers=request.headers.items(),
        query_params=request.query_params.items()
    )


@app.get('/')
def main_redirect():
    return RedirectResponse('/docs')

#
# if __name__ == 'main':  # pragma: no cover
#     import uvicorn
#
#     uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
#
