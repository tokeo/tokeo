"""
Tests for tokeo.ext.yaml: the errors of the config merge, a section that is
no dict and config files that do not merge.
"""

import pytest

from tokeo.main import TokeoTest
from tokeo.ext.yaml import TokeoYamlConfigError


class YamlTest(TokeoTest):
    """A test app with the yaml config handler, reading the given config files."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
        ]


# merge


def test_merge_refuses_a_section_that_is_no_dict():
    """A section holding a str raises TokeoYamlConfigError, also a ValueError."""
    with YamlTest() as app:
        with pytest.raises(ValueError, match='^config section "db" must be a dict, got "str"$') as err_info:
            app.config.merge({'db': 'plain'})
    assert type(err_info.value) is TokeoYamlConfigError


def test_merge_names_the_key_that_does_not_merge():
    """A str into a stored dict raises TokeoYamlConfigError naming the key."""
    with YamlTest() as app:
        app.config.merge({'db': {'opts': {'auth': {'user': 'a'}}}})
        with pytest.raises(TokeoYamlConfigError, match='^config "db.opts" does not merge: Cannot merge a str into a dict$') as err_info:
            app.config.merge({'db': {'opts': {'auth': 'plain'}}})
    assert type(err_info.value.__cause__) is ValueError


def test_merge_of_config_files_that_do_not_merge_fails(tmp_path):
    """A str in a second config file where the first has a dict stops the setup."""
    first = tmp_path / 'first.yaml'
    first.write_text('db:\n  opts:\n    auth:\n      user: a\n')
    second = tmp_path / 'second.yaml'
    second.write_text('db:\n  opts:\n    auth: plain\n')
    with pytest.raises(TokeoYamlConfigError, match='^config "db.opts" does not merge: Cannot merge a str into a dict$'):
        with YamlTest(config_files=[str(first), str(second)]):
            pass
