"""The browser front end.

Two things to hold on to. The engine must behave identically whichever front end
is driving it — one implementation, not two that drift. And the socket must
answer only to this application's own page: it is on loopback, but any web page
the user happens to have open can try to POST to a local port.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app import server
from app.main import Api
from tests import fixtures

CSV = (
    "Your full legal name,Child's current legal name,Child's date of birth\r\n"
    "SAMPLE PETITIONER,SAMPLE CHILD,3/2/2015\r\n"
)


@pytest.fixture
def live(registry):
    """A running server, and a helper that calls it the way the page does."""
    api = server.BrowserApi(registry)
    token = secrets.token_urlsafe(16)
    port = server.free_port(0)
    httpd = ThreadingHTTPServer((server.HOST, port), server.make_handler(api, token))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    base = f"http://{server.HOST}:{port}"

    def post(method, payload=None, tok=token, origin=None):
        headers = {"Content-Type": "application/json"}
        if tok is not None:
            headers["X-Docgen-Token"] = tok
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(
            f"{base}/api/{method}", data=json.dumps(payload or {}).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(path="/"):
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as reply:
            return reply.status, reply.read(), reply.headers

    try:
        yield type("Live", (), {"post": staticmethod(post), "get": staticmethod(get),
                                "token": token, "base": base, "api": api})
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------
# serving the page
# --------------------------------------------------------------------------


def test_the_page_is_served_with_its_token(live):
    status, body, headers = live.get("/")
    assert status == 200
    html = body.decode("utf-8")
    assert live.token in html
    assert "DOCGEN_TOKEN" in html
    # An edit to index.html must show up on refresh, or the whole point is lost.
    assert headers.get("Cache-Control") == "no-store"


def test_an_unknown_path_is_not_found(live):
    with pytest.raises(urllib.error.HTTPError) as exc:
        live.get("/nope.html")
    assert exc.value.code == 404


# --------------------------------------------------------------------------
# who is allowed to call it
# --------------------------------------------------------------------------


def test_a_call_without_the_token_is_refused(live):
    assert live.post("bootstrap", tok=None)[0] == 403


def test_a_call_with_the_wrong_token_is_refused(live):
    assert live.post("bootstrap", tok="not-the-token")[0] == 403


def test_a_call_from_another_website_is_refused(live):
    """A page on the internet can POST to a localhost port. It must get nowhere
    even if it somehow has the token."""
    assert live.post("bootstrap", origin="https://evil.example")[0] == 403


def test_the_applications_own_origin_is_accepted(live):
    assert live.post("bootstrap", origin=live.base)[0] == 200


def test_private_methods_are_not_reachable(live):
    assert live.post("_field_choices")[0] == 404


def test_an_unknown_action_is_not_found(live):
    assert live.post("no_such_action")[0] == 404


def test_an_unreadable_body_is_rejected_politely(live):
    request = urllib.request.Request(
        f"{live.base}/api/bootstrap", data=b"{not json",
        headers={"Content-Type": "application/json", "X-Docgen-Token": live.token},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=30)
    assert exc.value.code == 400


# --------------------------------------------------------------------------
# the same application, over a socket
# --------------------------------------------------------------------------


def test_bootstrap_says_which_front_end_this_is(live):
    status, res = live.post("bootstrap")
    assert status == 200 and res["ok"]
    assert res["mode"] == "browser"
    assert res["catalog"]


def test_the_desktop_bridge_says_the_other_thing(registry):
    assert Api(registry).bootstrap()["mode"] == "desktop"


def test_review_behaves_the_same_over_http(live, registry):
    _status, over_http = live.post(
        "review", {"matter": "dhs", "variant": "dhs_1p_1c", "values": fixtures.BASE}
    )
    direct = Api(registry).review(
        {"matter": "dhs", "variant": "dhs_1p_1c", "values": fixtures.BASE}
    )
    assert over_http == direct


def test_documents_are_generated_on_this_machine_not_in_the_browser(live, tmp_path, monkeypatch):
    monkeypatch.setattr("app.engine.OUTPUT_DIR", tmp_path)
    status, res = live.post(
        "generate", {"matter": "dhs", "variant": "dhs_1p_1c", "values": fixtures.BASE}
    )
    assert status == 200 and res["ok"], res.get("problems")

    written = list(tmp_path.rglob("*.docx"))
    assert len(written) == len(res["result"]["files"])
    assert any("Petition and Decree" in f.name for f in written)


def test_a_wrong_answer_comes_back_as_a_readable_problem(live):
    status, res = live.post("generate", {
        "matter": "dhs", "variant": "dhs_1p_1c",
        "values": {**fixtures.BASE, "county": "Atlantis"},
    })
    assert status == 200                      # the call worked; the intake did not
    assert res["ok"] is False
    assert any("Atlantis" in problem for problem in res["problems"])


# --------------------------------------------------------------------------
# the browser's file picker
# --------------------------------------------------------------------------


def encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_a_picked_file_is_copied_in_and_can_be_read(live):
    status, put = live.post("upload", {"name": "responses.csv", "data": encoded(CSV)})
    assert status == 200 and put["ok"]

    _status, res = live.post(
        "read_response", {"matter": "dhs", "variant": "dhs_1p_1c", "path": put["path"]}
    )
    assert res["ok"]
    assert [c["header"] for c in res["columns"]][0] == "Your full legal name"


def test_a_picked_file_imports_into_intake_answers(live):
    _s, put = live.post("upload", {"name": "responses.csv", "data": encoded(CSV)})
    _s, read = live.post("read_response", {"matter": "dhs", "variant": "dhs_1p_1c", "path": put["path"]})
    mapping = {str(c["index"]): c["field_id"] for c in read["columns"]}

    _s, imported = live.post("import_response", {
        "matter": "dhs", "variant": "dhs_1p_1c", "path": put["path"],
        "row": 0, "mapping": mapping, "remember": False,
    })
    assert imported["ok"]
    assert imported["values"]["child1_name"] == "SAMPLE CHILD"
    assert imported["values"]["child1_dob"] == "2015-03-02"


def test_an_upload_keeps_only_the_file_name(live):
    """A name is not a path. Anything with directories in it is stripped."""
    _status, put = live.post("upload", {"name": r"..\..\windows\system32\evil.csv", "data": encoded(CSV)})
    assert put["ok"]
    assert put["name"] == "evil.csv"
    assert "system32" not in put["path"]


def test_an_empty_upload_is_refused(live):
    _status, res = live.post("upload", {"name": "nothing.csv", "data": ""})
    assert res["ok"] is False
    assert "empty" in res["problems"][0]


def test_an_oversized_upload_is_refused(live):
    payload = base64.b64encode(b"x" * (server.MAX_UPLOAD + 32)).decode("ascii")
    _status, res = live.post("upload", {"name": "huge.csv", "data": payload})
    assert res["ok"] is False
    assert "larger than" in res["problems"][0]


def test_something_that_is_not_a_file_is_refused(live):
    _status, res = live.post("upload", {"name": "x.csv", "data": "!!! not base64 !!!"})
    assert res["ok"] is False


# --------------------------------------------------------------------------
# where it listens
# --------------------------------------------------------------------------


def test_it_binds_to_loopback_only():
    """Not 0.0.0.0. Adoption records must not be reachable from the network."""
    assert server.HOST == "127.0.0.1"


def test_a_free_port_is_found_even_when_the_usual_one_is_taken():
    with ThreadingHTTPServer((server.HOST, 0), server.make_handler(None, "t")) as taken:
        busy = taken.server_address[1]
        assert server.free_port(busy) != busy


# --------------------------------------------------------------------------
# reached through a forwarded port
# --------------------------------------------------------------------------


def test_the_page_works_when_reached_through_a_forwarded_name(live):
    """In a Codespace the page is served from a forwarded address, so its own
    calls carry that Origin. Insisting on loopback would refuse the application's
    own page and every button would fail."""
    forwarded = "https://sturdy-space-8765.app.github.dev"
    request = urllib.request.Request(
        f"{live.base}/api/bootstrap",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Docgen-Token": live.token,
            "Origin": forwarded,
            "Host": forwarded.removeprefix("https://"),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as reply:
        assert reply.status == 200
        assert json.loads(reply.read())["ok"]


def test_a_forwarded_name_still_refuses_a_different_site(live):
    """Same-origin is judged against the address the request arrived on, so a
    third-party page is refused however the app is reached."""
    request = urllib.request.Request(
        f"{live.base}/api/bootstrap",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Docgen-Token": live.token,
            "Origin": "https://evil.example",
            "Host": "sturdy-space-8765.app.github.dev",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=30)
    assert exc.value.code == 403


def test_it_still_binds_to_loopback_unless_told_otherwise():
    import inspect

    signature = inspect.signature(server.serve)
    assert signature.parameters["host"].default == server.HOST
