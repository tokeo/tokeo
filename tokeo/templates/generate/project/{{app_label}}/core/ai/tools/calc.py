"""
Calc tool for the {{ app_name }} ai agent.

A tiny, safe arithmetic tool that shows tool calling end to end with the
built-in mock provider. It evaluates a numeric expression with a restricted
AST walk, so no arbitrary code runs.

This module is self-contained: ``load`` registers the tool with the ai core
directly. Listing it in the app extensions after ``tokeo.ext.ai`` makes
``ai ask calc 2 + 3`` compute, so the otherwise empty mock gains a working
tool.
"""

import ast
import operator

from tokeo.core.ai import TokeoAiError, TokeoAiTool, register_tool


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node):
    # walk a tiny, numbers-only expression tree; anything else is rejected
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        return _UNOPS[type(node.op)](_eval(node.operand))
    raise TokeoAiError('calc supports only numbers and + - * / % **')


class TokeoAiCalcTool(TokeoAiTool):
    """
    Demo tool that evaluates a simple arithmetic expression.

    Shows tool calling end to end with the mock provider. The ``Meta``
    description and parameters are what the model sees; ``exec`` does the work
    with the safe evaluator above.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'evaluate a simple arithmetic expression'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(input=dict(type='string', description='the expression, e.g. 2 + 3')),
            required=['input'],
        )

    def exec(self, input):
        """
        Evaluate the expression and return the result as text.

        ### Args

        - **input** (str): The expression to evaluate, for example ``2 + 3``

        ### Returns

        - **str**: The numeric result

        ### Raises

        - **TokeoAiError**: If the expression is not a plain arithmetic term

        """
        # a plain string is accepted by the loop as the model-facing result
        try:
            return str(_eval(ast.parse(str(input), mode='eval').body))
        except TokeoAiError:
            raise
        except Exception:
            raise TokeoAiError(f'calc cannot evaluate {input!r}')


def load(app):
    """
    Load the calc tool, registering it with the ai core.

    ### Args

    - **app**: The application instance

    ### Notes

    : Registers the tool class directly; the registry is module-global, so no
        hook is needed. List this module after ``tokeo.ext.ai`` in the app
        extensions so ``ai ask calc 2 + 3`` computes.

    """
    # register this tool class with the ai core; the handler instantiates it
    # with the app on first use. the registry is a module-global dict, so a
    # direct call here is enough -- no hook required
    register_tool('calc', TokeoAiCalcTool)
