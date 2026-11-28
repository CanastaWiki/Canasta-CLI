"""read_env_file reads a .env as Compose does."""

import canasta_env_lines


def test_values_are_unquoted_as_compose_reads_them(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\nA=plain\nB=\"double\"\nC='single'\nD=\"unbalanced'\n"
        "E=x=y\nA=last\r\nnot a setting\n")
    assert canasta_env_lines.read_env_file(str(env)) == {
        "A": "last", "B": "double", "C": "single", "D": "\"unbalanced'",
        "E": "x=y"}


def test_a_missing_file_is_empty(tmp_path):
    assert canasta_env_lines.read_env_file(str(tmp_path / ".env")) == {}
