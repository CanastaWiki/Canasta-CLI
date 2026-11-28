"""The edge reads an instance's .env with the same parser as canasta_env."""

import canasta_edge_conversion as conversion


def test_quoted_values_are_unquoted(tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n"
        "PLAIN=value\n"
        "DOUBLE=\"two words\"\n"
        "SINGLE='it\\'s $literal'\n"
        "EMPTY=\n")
    env = conversion.read_env(str(tmp_path))
    assert env["PLAIN"] == "value"
    assert env["DOUBLE"] == "two words"
    assert env["SINGLE"] == "it's $literal"
    assert env["EMPTY"] == ""


def test_a_missing_env_is_empty(tmp_path):
    assert conversion.read_env(str(tmp_path)) == {}
