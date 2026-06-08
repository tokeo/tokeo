"""
NumPy inference for the {{ app_name }} fundi micro model.

The application runs the trained weights (``weights.npz``, created by
``make fundi-train``) with plain NumPy -- no torch, no server. Decoding is
greedy and grammar-constrained: at every step the next
byte must be legal plan DSL over the currently active tools, so the model can
never emit a malformed plan or a tool outside the injection (see ``dsl``).

If `numba <https://numba.pydata.org>`_ is installed, the hot attention loop
is JIT-compiled automatically; without it the pure NumPy path runs the same
numbers (the model is matmul-dominated, so BLAS does the heavy lifting
either way).

### How a plan is decoded

1. The request bytes plus the ``SEP`` token run through the net once; the
    keys and values of every layer are kept (the KV cache).
2. The output logits rank all bytes; the ``Constrainer`` says which
    characters are legal at this point of the plan grammar -- the best
    legal one wins (greedy), so the result is deterministic.
3. The chosen byte runs as a single-position forward against the cache,
    yielding the next logits; repeat until the grammar allows the end.

The weights file carries its architecture as embedded metadata, read at
load time -- the runtime adapts to whatever ``train.py`` exported.
"""

import json
import pathlib

import numpy

from {{ app_label }}.core.fundi import tokenizer
from {{ app_label }}.core.fundi.dsl import Constrainer

try:
    # optional acceleration: a no-op fallback keeps numpy the only need
    from numba import njit
except ImportError:   # pragma: no cover

    def njit(function=None, **kwargs):
        return function if function is not None else (lambda inner: inner)


WEIGHTS = pathlib.Path(__file__).parent / 'weights.npz'


@njit(cache=True)
def _attend(scores, values):   # pragma: no cover - numba compiles this
    # causal softmax-attention over one head; jit-friendly plain loops
    length = scores.shape[0]
    out = numpy.zeros_like(values)
    for row in range(length):
        line = scores[row, : row + 1]
        line = numpy.exp(line - line.max())
        line = line / line.sum()
        for col in range(row + 1):
            out[row] += line[col] * values[col]
    return out


class FundiModel:
    """
    The trained micro model: weights, forward pass, constrained decoding.

    """

    def __init__(self, path=None):
        bundle = numpy.load(path or WEIGHTS)
        self._weights = {name: bundle[name] for name in bundle.files if name != '__config__'}
        self._config = json.loads(bytes(bundle['__config__']).decode())

    @property
    def config(self):
        """The architecture and training metadata stored with the weights."""
        return dict(self._config)

    def plan(self, request, active=None):
        """
        Decode the plan DSL line for a request.

        ### Args

        - **request** (str): The user request
        - **active** (set | None): Active tool names; constrains the grammar

        ### Returns

        - **str**: A valid DSL line (or ``<nomatch>``)

        """
        context = self._config['context']
        tokens = tokenizer.encode(request.lower())[: context - 60] + [tokenizer.SEP]
        constrainer = Constrainer(active=active)
        # the prompt is primed once; every generated token then runs a
        # single-position forward against the cached keys and values
        cache = [[] for _ in range(self._config['layers'])]
        logits = self._forward(numpy.array(tokens, dtype=numpy.int64), cache)
        out = []
        for _ in range(58):
            allowed = constrainer.allowed()
            if allowed == {'<eos>'} or len(tokens) >= context - 1:
                break
            order = numpy.argsort(-logits)
            chosen = None
            for token in order:
                if token == tokenizer.EOS and '<eos>' in allowed:
                    chosen = None
                    break
                if token < 256 and chr(token) in allowed:
                    chosen = int(token)
                    break
            if chosen is None:
                break
            tokens.append(chosen)
            out.append(chosen)
            constrainer.feed(chr(chosen))
            logits = self._step(chosen, len(tokens) - 1, cache)
        return tokenizer.decode(out) or ''

    def _forward(self, tokens, cache=None):
        # the full forward pass priming the cache; matmul-dominated, so
        # plain numpy with blas carries it
        weights = self._weights
        config = self._config
        hidden = weights['embed.weight'][tokens] + weights['position.weight'][: len(tokens)]
        for layer in range(config['layers']):
            hidden = self._block(hidden, layer, cache)
        hidden = _layer_norm(hidden, weights['ln.weight'], weights['ln.bias'])
        return hidden[-1] @ weights['embed.weight'].T

    def _step(self, token, position, cache):
        # one-position forward over the cached keys and values
        weights = self._weights
        config = self._config
        hidden = weights['embed.weight'][token] + weights['position.weight'][position]
        hidden = hidden[None, :]
        for layer in range(config['layers']):
            hidden = self._block(hidden, layer, cache, step=True)
        hidden = _layer_norm(hidden, weights['ln.weight'], weights['ln.bias'])
        return (hidden[-1] @ weights['embed.weight'].T)

    def _block(self, hidden, layer, cache=None, step=False):
        weights = self._weights
        config = self._config
        prefix = f'blocks.{layer}.'
        normed = _layer_norm(hidden, weights[prefix + 'ln1.weight'], weights[prefix + 'ln1.bias'])
        attended = self._attention(normed, prefix, config, None if cache is None else cache[layer], step)
        hidden = hidden + attended
        normed = _layer_norm(hidden, weights[prefix + 'ln2.weight'], weights[prefix + 'ln2.bias'])
        inner = normed @ weights[prefix + 'mlp.0.weight'].T + weights[prefix + 'mlp.0.bias']
        inner = _gelu(inner)
        return hidden + inner @ weights[prefix + 'mlp.2.weight'].T + weights[prefix + 'mlp.2.bias']

    def _attention(self, normed, prefix, config, layer_cache, step):
        weights = self._weights
        heads = config['heads']
        dim = config['dim']
        size = dim // heads
        qkv = normed @ weights[prefix + 'attn.in_proj_weight'].T + weights[prefix + 'attn.in_proj_bias']
        query, key, value = numpy.split(qkv, 3, axis=-1)
        if layer_cache is not None:
            if step:
                key = numpy.concatenate([layer_cache[0], key])
                value = numpy.concatenate([layer_cache[1], value])
            layer_cache[:] = [key, value]
        out = numpy.empty_like(query)
        for head in range(heads):
            sliced = slice(head * size, (head + 1) * size)
            scores = (query[:, sliced] @ key[:, sliced].T) / numpy.sqrt(size)
            if step:
                # a single query attends over everything cached: a plain
                # softmax row, no causal loop needed
                line = numpy.exp(scores[0] - scores[0].max())
                out[:, sliced] = (line / line.sum()) @ value[:, sliced]
            else:
                out[:, sliced] = _attend(scores.astype(numpy.float32), value[:, sliced].astype(numpy.float32))
        return out @ weights[prefix + 'attn.out_proj.weight'].T + weights[prefix + 'attn.out_proj.bias']


def _layer_norm(x, gain, bias):
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / numpy.sqrt(var + 1e-5) * gain + bias


def _gelu(x):
    return 0.5 * x * (1.0 + numpy.tanh(numpy.sqrt(2.0 / numpy.pi) * (x + 0.044715 * x ** 3)))
