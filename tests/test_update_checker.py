from zomercompetitie.update_checker import is_newer_version


def test_is_newer_version_handles_semver_and_v_prefix():
    assert is_newer_version("v0.2.0", "0.1.0") is True
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("1.0.1", "1.0.9") is False
    assert is_newer_version("2.0", "1.9.9") is True
