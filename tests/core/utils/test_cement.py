"""
Tests for tokeo.core.utils.cement.
"""

import pytest

from tokeo.main import TokeoTest
from tokeo.core.exc import TokeoError
from tokeo.core.utils.cement.extensions import required as extensions_required


class CementTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.tui.output',
        ]


def test_extensions_required_names_the_missing_ones():
    with CementTest() as app:
        extensions_required(app, 'tokeo.ext.probe', 'tokeo.ext.tui.output')
        with pytest.raises(TokeoError, match='needs tokeo.ext.tui.input and tokeo.ext.x, add them to the extensions of the app!'):
            extensions_required(app, 'tokeo.ext.probe', 'tokeo.ext.tui.input', 'tokeo.ext.tui.output', 'tokeo.ext.x')
