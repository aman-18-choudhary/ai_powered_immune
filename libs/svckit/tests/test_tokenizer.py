from svckit.tokenizer import Tokenizer


def test_token_is_deterministic_and_differs_by_kind():
    t = Tokenizer(b"secret")
    assert t.token("alice@x.com", "email") == t.token("alice@x.com", "email")
    assert t.token("alice@x.com", "email") != t.token("alice@x.com", "phone")
    assert t.token("a", "email") != t.token("b", "email")
    assert Tokenizer(b"other").token("a", "email") != t.token("a", "email")


def test_token_does_not_contain_raw_value():
    tok = Tokenizer(b"secret").token("alice@x.com", "email")
    assert tok.startswith("tok_")
    assert "alice" not in tok
    assert not hasattr(Tokenizer(b"s"), "reverse")
