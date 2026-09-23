"""Token tests: opaque, no collision with original, stable per context."""
from app.core.tokens import TokenContext, TokenGenerator


class TestTokens:
    def test_opaque_and_random(self):
        gen = TokenGenerator(TokenContext("ns", "ctx1"))
        t1 = gen.token_for("EMAIL", "a@b.com")
        t2 = gen.token_for("EMAIL", "a@b.com")
        assert t1 == t2  # stable within context
        assert "a@b.com" not in t1
        assert t1.startswith("⟦PII:EMAIL:")

    def test_different_contexts_no_global_token(self):
        g1 = TokenGenerator(TokenContext("ns1", "ctx1"))
        g2 = TokenGenerator(TokenContext("ns2", "ctx1"))
        t1 = g1.token_for("EMAIL", "a@b.com")
        t2 = g2.token_for("EMAIL", "a@b.com")
        assert t1 != t2

    def test_128_bits_randomness(self):
        gen = TokenGenerator(TokenContext("ns", "ctx"))
        t = gen.token_for("CARD", "4276189074144957")
        opaque = t.split(":")[2].rstrip("⟧")
        # token_urlsafe(16) -> 16 bytes = 128 bits, base64 ~22 chars.
        assert len(opaque) >= 20

    def test_ensure_not_in_text(self):
        gen = TokenGenerator(TokenContext("ns", "ctx"))
        token = gen.token_for("EMAIL", "a@b.com")
        # If the token happened to appear, ensure_not_in_text regenerates.
        result = gen.ensure_not_in_text(token, "some text without token")
        assert result == token  # unchanged when not present
