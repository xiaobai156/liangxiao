from __future__ import annotations

import subprocess
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

import fetching.browser as browser
import fetching.client as client
from domain.models import Site
from fetching.page import collect_page_and_scripts
from fetching.urls import FetchedText, SourceBoundaryError, validate_redirect


def response(url, status=200, *, location="", body=b"100\xe6\x9c\x9f"):
    value = requests.Response()
    value.url = url
    value.status_code = status
    value._content = body
    value._content_consumed = True
    value.encoding = "utf-8"
    if location:
        value.headers["Location"] = location
    return value


def session_for(monkeypatch, responses):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs["verify"] is True
        assert kwargs["allow_redirects"] is False
        return responses[url]
    monkeypatch.setattr(client, "HTTP_SESSION", SimpleNamespace(get=get))
    return calls


def test_http_validates_tls_and_retains_redirect_provenance(monkeypatch):
    start, final = "https://example.test/topic/100", "https://example.test/topic/100.html"
    calls = session_for(monkeypatch, {
        start: response(start, 302, location=final), final: response(final),
    })
    text = client.fetch_text(start, 2)
    assert text == "100期"
    assert text.requested_url == start
    assert text.final_url == final
    assert text.redirect_chain == (start, final)
    assert [url for url, _ in calls] == [start, final]


@pytest.mark.parametrize("target", [
    "https://evil.test/topic/100.html", "http://example.test/topic/100.html",
    "https://example.test/topic/101.html", "https://example.test/login",
    "https://example.test/topic/100.html?id=101", "file:///tmp/page",
    "https://username:password@example.test/topic/100.html",
])
def test_forbidden_redirect_is_rejected_before_requesting_target(monkeypatch, target):
    start = "https://example.test/topic/100.html"
    calls = session_for(monkeypatch, {start: response(start, 302, location=target)})
    with pytest.raises(SourceBoundaryError):
        client.fetch_text(start, 2)
    assert len(calls) == 1


@pytest.mark.parametrize(("before", "after"), [
    ("https://a.test/view.php?id=1", "https://a.test/view.php?id=2"),
    ("https://a.test/read.php?tid=1", "https://a.test/read.php"),
    ("https://a.test/article/manager/abc", "https://a.test/article/manager/xyz"),
    ("https://a.test/api/proxy/admin-articles/abc", "https://a.test/api/proxy/admin-articles/xyz"),
    ("https://a.test/api/v1/users/1/forums", "https://a.test/api/v1/users/2/forums"),
    ("https://a.test/list.aspx?id=1&page=2", "https://a.test/list.aspx?id=1&page=3"),
])
def test_redirect_record_and_listing_id_boundaries(before, after):
    with pytest.raises(SourceBoundaryError):
        validate_redirect(before, after)


def test_explicit_redirect_origin_does_not_relax_record_identity():
    before, after = "https://a.test/topic/1", "https://b.test/topic/1.html"
    validate_redirect(before, after, ("https://b.test",))
    with pytest.raises(SourceBoundaryError):
        validate_redirect(before, after.replace("/1.html", "/2.html"), ("https://b.test",))
    with pytest.raises(SourceBoundaryError):
        validate_redirect(before, after.replace("https:", "http:"), ("http://b.test",))


def test_same_host_http_to_https_upgrade_is_allowed():
    validate_redirect("http://a.test/topic/1", "https://a.test/topic/1")


def test_redirect_query_reordering_preserves_identity():
    validate_redirect("https://a.test/article.asp?ListId=2&id=1",
                      "https://a.test/article.asp?id=1&ListId=2")


def test_redirect_loop_has_bounded_requests(monkeypatch):
    a, b = "https://a.test/page", "https://a.test/page/"
    calls = session_for(monkeypatch, {a: response(a, 302, location=b), b: response(b, 302, location=a)})
    with pytest.raises(SourceBoundaryError, match="循环"):
        client.http_get(a, 2)
    assert len(calls) == 2


@pytest.mark.parametrize("exception", [requests.exceptions.SSLError, requests.exceptions.ChunkedEncodingError])
def test_request_failure_never_silently_falls_back_to_insecure_curl(monkeypatch, exception):
    get = Mock(side_effect=exception("broken"))
    curl = Mock(side_effect=AssertionError("insecure fallback"))
    monkeypatch.setattr(client, "http_get", get)
    monkeypatch.setattr(client, "fetch_curl_text", curl)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    with pytest.raises(exception):
        client.fetch_text("https://a.test/page", 2)
    assert curl.call_count == 0
    assert get.call_count == (1 if exception is requests.exceptions.SSLError else 3)


def test_fetch_context_separates_redirect_permission_cache_keys():
    url = "https://a.test/topic/1"
    fetch = Mock(return_value=FetchedText("body", url, "https://b.test/topic/1"))
    context = client.FetchContext(text_fetcher=fetch)
    assert context.get_text(url, 2, allowed_origins=("https://b.test",)) == "body"
    with pytest.raises(SourceBoundaryError):
        context.get_text(url, 2)
    assert fetch.call_count == 2


def test_closed_context_rejects_even_cached_payload():
    context = client.FetchContext(text_fetcher=lambda *_: "body", renderer=lambda *_: "rendered")
    context.get_text("https://a.test/page", 2)
    context.get_rendered("https://a.test/page", 2)
    context.close()
    with pytest.raises(requests.RequestException, match="关闭"):
        context.get_text("https://a.test/page", 2)
    with pytest.raises(requests.RequestException, match="关闭"):
        context.get_rendered("https://a.test/page", 2)


def curl_output(body, status, final, redirect=""):
    return body + client.CURL_META_MARKER + f"{status}\n{final}\n{redirect}".encode()


@pytest.mark.parametrize("insecure", [False, True])
def test_curl_insecure_mode_is_explicit_and_never_uses_dash_L(monkeypatch, insecure):
    url = "https://a.test/page"
    def run(command, **kwargs):
        assert ("-k" in command) is insecure
        assert "-L" not in command
        assert kwargs["timeout"] == 7
        return subprocess.CompletedProcess(command, 0, stdout=curl_output(b"body", 200, url))
    monkeypatch.setattr(client.subprocess, "run", run)
    value = client.fetch_curl_text(url, 2, insecure=insecure)
    assert value == "body" and value.final_url == url


def test_curl_checks_redirect_before_request(monkeypatch):
    url = "https://a.test/page"
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=curl_output(b"", 302, url, "https://evil.test/page")))
    monkeypatch.setattr(client.subprocess, "run", run)
    with pytest.raises(SourceBoundaryError):
        client.fetch_curl_text(url, 2, insecure=True)
    assert run.call_count == 1


def test_curl_preserves_404_status_for_explicit_api_fallback(monkeypatch):
    url = "https://a.test/page"
    monkeypatch.setattr(client.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, stdout=curl_output(b"error", 404, url))))
    with pytest.raises(requests.HTTPError) as caught:
        client.fetch_curl_text(url, 2)
    assert caught.value.response.status_code == 404


def test_curl_refuses_missing_status_metadata(monkeypatch):
    monkeypatch.setattr(client.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, stdout=b"looks like valid data")))
    with pytest.raises(SourceBoundaryError):
        client.fetch_curl_text("https://a.test/page", 2)


def test_unconfigured_cross_origin_data_script_is_not_requested():
    site = Site("站", "top", "https://a.test/page", payload="page_and_scripts")
    fetch = Mock(side_effect=AssertionError("unauthorized network call"))
    bundle = collect_page_and_scripts(site, '<script src="https://evil.test/upload/script/data.js"></script>',
                                      2, client.FetchContext(text_fetcher=fetch))
    assert fetch.call_count == 0
    assert len(bundle.documents) == 1


def test_allowed_document_origin_is_explicit():
    site = Site("站", "top", "https://a.test/page", payload="page_and_scripts", allowed_document_origins=("https://cdn.test",))
    fetch = Mock(return_value="站 100期绝杀二肖【虎兔】开00")
    collect_page_and_scripts(site, '<script src="https://cdn.test/upload/script/data.js"></script>',
                             2, client.FetchContext(text_fetcher=fetch))
    assert fetch.call_count == 1


def test_pattern_cannot_be_smuggled_inside_an_untrusted_query():
    site = Site("站", "top", "https://a.test/page", payload="page_and_scripts", linked_document_pattern=r"https://cdn\.test/data/")
    fetch = Mock(side_effect=AssertionError("unauthorized request"))
    collect_page_and_scripts(site, '<script src="https://evil.test/data.js?url=https://cdn.test/data/a.js"></script>',
                             2, client.FetchContext(text_fetcher=fetch))
    assert fetch.call_count == 0


def test_children_resolve_against_actual_final_page_url():
    site = Site("站", "top", "https://a.test/topic/1", payload="page_and_scripts")
    source = FetchedText('<script src="data.js"></script>', site.url, "https://a.test/other/topic/1")
    fetch = Mock(return_value="script")
    bundle = collect_page_and_scripts(site, source, 2, client.FetchContext(text_fetcher=fetch))
    assert fetch.call_args.args[0] == "https://a.test/other/topic/data.js"
    assert bundle.documents[0].url == "https://a.test/other/topic/1"


def test_browser_blocked_worker_and_close_are_bounded(monkeypatch):
    entered, release = Event(), Event()
    context = SimpleNamespace(close=lambda: None)
    runtime = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_: SimpleNamespace(new_context=lambda **_: context, close=lambda: None)), stop=lambda: None)
    monkeypatch.setattr(browser, "sync_playwright", lambda: SimpleNamespace(start=lambda: runtime))
    def stuck(*_):
        entered.set()
        release.wait(3)
        return "late"
    monkeypatch.setattr(browser, "_render_page", stuck)
    monkeypatch.setattr(browser, "RENDER_WAIT_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(browser, "SHUTDOWN_TIMEOUT_SECONDS", 0.02)
    renderer = browser.PlaywrightRenderer()
    try:
        with pytest.raises(requests.Timeout):
            renderer("https://a.test/page", 0)
        assert entered.is_set()
        with pytest.raises(requests.Timeout):
            renderer("https://a.test/other", 0)
        renderer.close()
        assert renderer._closed
    finally:
        release.set()
        renderer.close()
        renderer._thread.join(3)
    assert not renderer._thread.is_alive()


def test_browser_checks_client_side_final_url_and_closes_page():
    page = SimpleNamespace(url="https://evil.test/page", main_frame=object())
    page.route = lambda *_: None
    page.goto = lambda *_a, **_k: None
    page.close = Mock()
    with pytest.raises(SourceBoundaryError):
        browser._render_page(SimpleNamespace(new_page=lambda: page), "https://a.test/page", 1)
    assert page.close.call_count == 1


def test_explicit_legacy_transport_is_scoped_to_its_site_and_separate_cache(monkeypatch):
    url = 'https://a.test/upload/script/body.js'
    strict = Mock(return_value=FetchedText('strict body', url, url))
    legacy = Mock(return_value=FetchedText('legacy body', url, url))
    monkeypatch.setattr(client, 'fetch_text', strict)
    monkeypatch.setattr(client, 'fetch_curl_text', legacy)
    context = client.FetchContext()
    site = Site('旧TLS站', 'top', 'https://a.test/page', payload='curl_tls10_page_and_scripts')
    assert client.get_site_text(context, url, 2, site) == 'legacy body'
    assert client.get_site_text(context, url, 2, site) == 'legacy body'
    assert context.get_text(url, 2) == 'strict body'
    legacy.assert_called_once_with(url, 2, insecure=True, allowed_origins=())
    strict.assert_called_once_with(url, 2)


@pytest.mark.parametrize('target', ['https://a.test/view.php?id=1&id=2',
                                    'https://a.test/view.php?id=1&ID=2'])
def test_ambiguous_identity_query_parameters_are_rejected(target):
    with pytest.raises(SourceBoundaryError, match='身份参数不唯一'):
        validate_redirect('https://a.test/view.php?id=1', target)
