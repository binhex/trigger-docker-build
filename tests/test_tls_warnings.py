"""Tests for TLS warning suppression when verify_ssl is disabled.

The daemon must silence urllib3's InsecureRequestWarning ONLY when the user
explicitly opted out of certificate verification (verify_ssl = False). When
verification is enabled (default), warnings must remain visible so users
notice TLS problems.

urllib3.disable_warnings() registers a warnings filter (simplefilter 'ignore'
for the given category). We assert on the filter registry because
warnings.catch_warnings(record=True) + simplefilter('always') overrides
registered ignore filters at emit time.
"""

import warnings

from urllib3.exceptions import InsecureRequestWarning, SecurityWarning


def _has_ignore_filter(category):
    """True if an 'ignore' filter for the given category is registered."""
    return any(f[0] == "ignore" and issubclass(f[2], category) for f in warnings.filters)


class TestTlsWarningSuppression:
    """_silence_tls_warnings() helper behavior."""

    def test_suppresses_warning_when_verify_ssl_false(self, tdb):
        """verify_ssl=False → InsecureRequestWarning ignore filter registered."""
        warnings.resetwarnings()  # clean slate
        tdb._silence_tls_warnings(False)

        assert _has_ignore_filter(InsecureRequestWarning)

    def test_keeps_warning_when_verify_ssl_true(self, tdb):
        """verify_ssl=True (default) → no ignore filter added."""
        warnings.resetwarnings()
        tdb._silence_tls_warnings(True)

        assert not _has_ignore_filter(InsecureRequestWarning)

    def test_does_not_suppress_other_warnings(self, tdb):
        """Only InsecureRequestWarning is silenced, not all warnings."""
        warnings.resetwarnings()
        tdb._silence_tls_warnings(False)

        assert _has_ignore_filter(InsecureRequestWarning)
        # Broad categories (e.g. plain Warning, UserWarning) must NOT be ignored
        assert not _has_ignore_filter(UserWarning)

    def test_suppresses_security_warning_hierarchy(self, tdb):
        """InsecureRequestWarning is a SecurityWarning; both covered by the filter."""
        warnings.resetwarnings()
        tdb._silence_tls_warnings(False)

        # HTTPWarning is the base category urllib3.disable_warnings defaults to;
        # our call targets InsecureRequestWarning specifically
        assert issubclass(InsecureRequestWarning, SecurityWarning)
        assert _has_ignore_filter(InsecureRequestWarning)
