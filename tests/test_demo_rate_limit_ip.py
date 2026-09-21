from starlette.requests import Request

from app import demo


def _request(headers: dict[str, str], client=("5.5.5.5", 1234)) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_uses_last_forwarded_entry_not_client_forged_first():
    req = _request({"X-Forwarded-For": "9.9.9.9, 1.2.3.4"})
    assert demo._client_ip(req) == "1.2.3.4"


def test_prepended_fake_entry_shares_bucket_with_real_ip():
    a = _request({"X-Forwarded-For": "1.2.3.4"})
    b = _request({"X-Forwarded-For": "9.9.9.9, 1.2.3.4"})
    assert demo._client_ip(a) == demo._client_ip(b) == "1.2.3.4"


def test_rate_limit_bucket_key_is_last_entry():
    demo._hits.clear()
    demo.rate_limit(_request({"X-Forwarded-For": "9.9.9.9, 1.2.3.4"}))
    assert list(demo._hits) == ["1.2.3.4"]
    demo._hits.clear()


def test_falls_back_to_connection_host_without_header():
    assert demo._client_ip(_request({})) == "5.5.5.5"
