import json

import js
from pyodide.ffi import to_js
from pyodide.http import pyfetch

from core.app import App

session = None
report = None


async def fetch(method, url, headers, body):
    options = {"method": method, "headers": headers}
    if body is not None:
        options["body"] = body
    response = await pyfetch(url, **options)
    return response.status, await response.bytes()


def _progress(done, total, idx=-1):
    report(done, total, idx)


def start(get_token, on_progress, api_base):
    global session, report
    report = on_progress
    session = App(get_token, fetch, api_base)


async def create(password, accounts_json):
    await session.create(password, json.loads(accounts_json))


async def open_vault(password, accounts_json):
    await session.open(password, json.loads(accounts_json))


async def release():
    await session.release()


async def renew():
    await session.renew()


async def add_account(email):
    await session.add_account(email)


def state():
    return json.dumps(session.state())


async def upload(file, remote_path):
    async def read_at(offset, n):
        buffer = await file.slice(offset, offset + n).arrayBuffer()
        return buffer.to_bytes()

    await session.upload(read_at, int(file.size), remote_path, _progress)


async def download(remote_path):
    parts = []
    await session.download(remote_path, lambda data: parts.append(to_js(data)), _progress)
    options = to_js({"type": "application/octet-stream"}, dict_converter=js.Object.fromEntries)
    return js.Blob.new(to_js(parts), options)


async def download_folder(remote_path):
    data = await session.download_folder(remote_path, _progress)
    options = to_js({"type": "application/zip"}, dict_converter=js.Object.fromEntries)
    return js.Blob.new(to_js([to_js(data)]), options)


async def delete_file(remote_path):
    await session.delete_file(remote_path, _progress)


async def delete_folder(remote_path):
    await session.delete_folder(remote_path, _progress)


async def change_password(password):
    await session.change_password(password)


async def remove_account(email):
    await session.remove_account(email)


def search(query, exact, use_regex, filter_type):
    return json.dumps(session.search(query, exact, use_regex, filter_type or None))
