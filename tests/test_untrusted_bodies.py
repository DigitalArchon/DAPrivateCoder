"""What is read from outside is read only so far, as every DA app reads it (the cross-app standard, Nov 2026, as DA
Toolkit's 150a14b): only gzip and deflate asked for and any other coding refused unread, an error's body read to
64 KB, a whole body to 32 MB (both counted decoded), and JSON nested no deeper than 200 levels, never a
RecursionError: llm/secure_http.py (make_client, loads), llm/client.py (capped_lines, reading_options), and every
client and reader built on them: the SDK's, Private Mode's, the listings, search, the proxy's own, Claude Code's
requests to the proxy, the window's API, the WebFetch hook and the search server."""

import asyncio
import gzip
import io
import json

import httpx
import httpx2
import pytest

from daprivatecoder.llm import client as client_mod
from daprivatecoder.llm import private_mode, secure_http, tee
from daprivatecoder.llm.client import LLMClient, capped_lines, reading_options, sdk_http_client
from daprivatecoder.llm.secure_http import MAX_NESTING, ProviderError, TooDeep, loads, make_client, nests_deeper

from test_private_mode import BASE, RELAY, Relay

USER = [{"role": "user", "content": "hi"}]


def nested(depth: int) -> str:
    return "[" * depth + "]" * depth


def pieces(data: bytes, size: int = 4096):
    """A body as a connection hands it over, a piece at a time (bytes given whole are read at once)."""
    return iter([data[i:i + size] for i in range(0, len(data), size)])


async def apieces(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i:i + size]


@pytest.fixture(autouse=True)
def no_proxies(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_json_from_outside_is_refused_past_two_hundred_levels():
    assert loads(nested(MAX_NESTING)) is not None and not nests_deeper(json.loads(nested(MAX_NESTING)))
    with pytest.raises(TooDeep):
        loads(nested(MAX_NESTING + 1))
    with pytest.raises(TooDeep):                    # past the parser's own reach: a refusal, not a RecursionError
        loads(nested(100_000))


# ------------------------------------------------------------------- the attestation client

def sync_client(handler) -> httpx.Client:
    client = make_client("https://x.test")
    client._transport = httpx.MockTransport(handler)
    return client


def test_the_attestation_client_asks_for_gzip_and_deflate_alone_and_refuses_another_coding():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})
    sync_client(handler).get("https://x.test/")
    assert seen["accept-encoding"] == "gzip, deflate"
    with pytest.raises(ProviderError, match="compressed as 'br'"):
        sync_client(lambda r: httpx.Response(200, content=b"x", headers={"content-encoding": "br"})).get(
            "https://x.test/")


def test_the_attestation_client_reads_an_error_to_64_kb_and_a_body_to_32_mb(monkeypatch):
    big = b"e" * (secure_http.MAX_ERROR_BYTES * 10)
    assert len(sync_client(lambda r: httpx.Response(500, content=pieces(big))).get("https://x.test/").content) \
        == secure_http.MAX_ERROR_BYTES
    monkeypatch.setattr(secure_http, "MAX_BODY_BYTES", 100_000)
    packed = gzip.compress(b"0" * 1_000_000)
    with pytest.raises(ProviderError, match="sent more than"):
        sync_client(lambda r: httpx.Response(200, content=pieces(packed), headers={"content-encoding": "gzip"})).get(
            "https://x.test/")


def test_a_session_record_nested_past_reading_is_a_note_never_a_crash():
    client = sync_client(lambda r: httpx.Response(200, content=nested(50_000).encode()))
    with pytest.raises(ProviderError, match="couldn't be read"):
        tee._session_record(client, "ab" * 32)


# ------------------------------------------------------------------- the async clients

async def read_through_hook(response: httpx.Response) -> bytes:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response),
                                 event_hooks={"response": [capped_lines]}) as client:
        return (await client.get("https://x.test/")).content


async def test_an_error_body_is_read_to_64_kb_and_a_whole_body_to_32_mb(monkeypatch):
    body = await read_through_hook(httpx.Response(400, content=apieces(b"e" * (secure_http.MAX_ERROR_BYTES * 4))))
    assert len(body) == secure_http.MAX_ERROR_BYTES
    monkeypatch.setattr(client_mod, "MAX_BODY_BYTES", 50_000)
    with pytest.raises(ProviderError, match="sent more than"):
        await read_through_hook(httpx.Response(200, content=apieces(b"line\n" * 20_000),
                                               headers={"content-type": "application/json"}))
    stream = await read_through_hook(httpx.Response(200, content=apieces(b"data: x\n\n" * 20_000),
                                                    headers={"content-type": "text/event-stream"}))
    assert len(stream) == 9 * 20_000                # an event stream is held to its lines, not its length
    with pytest.raises(ProviderError, match="compressed as 'zstd'"):
        await read_through_hook(httpx.Response(200, content=b"x", headers={"content-encoding": "zstd"}))


async def test_a_reply_asked_for_whole_is_held_to_32_mb_whatever_its_content_type(monkeypatch):
    """LLMClient.complete asks for a reply not streamed (the SDK 3.46's client is httpx2's): one that calls itself
    an event stream is read no further than a whole body is."""
    monkeypatch.setattr(client_mod, "MAX_BODY_BYTES", 50_000)
    client = LLMClient(BASE, "k", sdk_http_client(BASE, transport=httpx2.MockTransport(lambda request: httpx2.Response(
        200, content=apieces(b"data: x\n\n" * 20_000), headers={"content-type": "text/event-stream"}))))
    with pytest.raises(ProviderError, match="sent more than"):
        await client.complete("z-ai/glm-5.3", USER)


async def test_the_sdks_client_asks_for_gzip_and_deflate_alone():
    seen = []

    def answer(request):
        seen.append(request.headers.get("accept-encoding"))
        return httpx2.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                          "choices": [{"index": 0, "finish_reason": "stop",
                                                       "message": {"role": "assistant", "content": "ok"}}]})
    client = LLMClient(BASE, "k", sdk_http_client(BASE, transport=httpx2.MockTransport(answer)))
    assert await client.complete("z-ai/glm-5.3", USER) == "ok"
    assert seen == ["gzip, deflate"]
    assert reading_options(BASE)["headers"] == {"Accept-Encoding": "gzip, deflate"}


# ------------------------------------------------------------------- Private Mode

async def test_the_relays_error_body_is_read_to_64_kb_and_its_preflight_json_bounded():
    relay = Relay()
    client = relay.client()
    client._transport_factory = lambda key, proxy=None: httpx.MockTransport(lambda request: httpx.Response(
        500, content=apieces(b"x" * (secure_http.MAX_ERROR_BYTES * 3))))
    with pytest.raises(private_mode.PrivateModeError) as raised:
        [event async for event in client.stream("private/glm-5-3", USER)]
    assert len(raised.value.body) == secure_http.MAX_ERROR_BYTES
    deep = relay.client()
    deep._plain = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: relay.preflight(r) if r.url.path.endswith("/models") else httpx.Response(200, text=nested(50_000))))
    with pytest.raises(private_mode.PrivateModeError, match="wasn't JSON"):
        [event async for event in deep.stream("private/glm-5-3", USER)]
    assert RELAY.endswith("/private/tinfoil")


# ------------------------------------------------------------------- what reaches the app from outside

async def test_claude_codes_request_nested_past_two_hundred_levels_is_refused_in_words():
    from test_proxy import call, make, message
    p, _ = make()
    body = json.dumps({**message(), "metadata": json.loads(nested(300))})
    r = await call(p, "POST", "/v1/messages", raw=body)
    assert r.status_code == 400 and "isn't JSON" in r.text
    r = await call(p, "POST", "/v1/messages", raw='{"model": ' + nested(100_000) + "}")
    assert r.status_code == 400


async def test_the_proxys_own_client_asks_for_gzip_and_deflate_and_reads_only_so_far():
    from daprivatecoder.proxy import Proxy, read_capped, refuse_insecure
    from test_proxy import PRIVATE, TOKEN
    p = Proxy(PRIVATE, BASE, TOKEN, key=lambda: "k")
    own = p._client("https://nano-gpt.com/api/v1")
    assert own.headers["accept-encoding"] == "gzip, deflate"
    assert own.event_hooks["response"] == [refuse_insecure, read_capped]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, content=b"x", headers={"content-encoding": "br"})), event_hooks={"response": [read_capped]}) as c:
        with pytest.raises(httpx.HTTPError, match="compressed as 'br'"):    # as the proxy's callers take an error
            await c.get("https://x.test/")
    await own.aclose()


async def test_the_windows_api_refuses_a_body_nested_past_two_hundred_levels():
    from daprivatecoder import manager
    from daprivatecoder.server import create_app
    app = create_app("tok", manager.Manager(), on_quit=lambda: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        for depth, status in ((150, 200), (300, 400), (100_000, 400)):
            body = '{"theme": "dark", "more": ' + nested(depth) + "}"
            r = await c.post("/api/theme", headers={"X-Token": "tok"}, content=body)
            assert r.status_code == status, (depth, r.status_code, r.text[:200])


def test_the_webfetch_hook_refuses_what_it_cant_read_however_deep():
    """A hook that fails lets Claude Code go on: an event nested past the parser's reach is refused, not a crash."""
    from daprivatecoder import identity
    out = io.StringIO()
    assert identity.fetch_hook(io.StringIO('{"tool_input": ' + nested(100_000) + "}"), out) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_the_search_server_answers_a_message_nested_past_reading_as_no_json(monkeypatch):
    """Claude Code's messages to the search server, one a line on its stdin: one nested past 200 levels is
    answered "Not JSON.", and one past the parser's own reach too, never a crash of the server."""
    import os
    import sys

    from daprivatecoder import search

    read, write = os.pipe()
    os.write(write, (nested(300) + "\n" + nested(5000) + "\n").encode())
    os.close(write)

    class Out:
        buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", os.fdopen(read, "rb", buffering=0))
    monkeypatch.setattr(sys, "stdout", Out)
    await asyncio.wait_for(search.serve_stdio(object()), 10)
    replies = [json.loads(line) for line in Out.buffer.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies] == [-32700, -32700]


def test_a_redirects_own_body_is_read_to_64_kb_followed_or_not():
    """httpx reads a redirect it follows whole before following it: read only so far, as an error's
    (DA Toolkit's 65dc490)."""
    sent = []

    def handler(request):
        if request.url.path == "/moved":
            body = b"r" * (secure_http.MAX_ERROR_BYTES * 16)
            sent.append(0)

            def counted():
                for piece in pieces(body):
                    sent[-1] += len(piece)
                    yield piece
            return httpx.Response(302, headers={"location": "/here"}, content=counted())
        return httpx.Response(200, content=b"fine")
    client = sync_client(handler)
    followed = client.get("https://x.test/moved", follow_redirects=True)
    assert followed.text == "fine" and len(followed.history[0].content) == secure_http.MAX_ERROR_BYTES
    assert sent[0] <= secure_http.MAX_ERROR_BYTES + 4096, "the rest unread"
    assert len(client.get("https://x.test/moved").content) == secure_http.MAX_ERROR_BYTES


async def test_a_redirects_body_through_the_sdks_hook_is_read_to_64_kb():
    body = await read_through_hook(httpx.Response(
        307, headers={"location": "https://x.test/there"}, content=apieces(b"r" * (secure_http.MAX_ERROR_BYTES * 4))))
    assert len(body) == secure_http.MAX_ERROR_BYTES
