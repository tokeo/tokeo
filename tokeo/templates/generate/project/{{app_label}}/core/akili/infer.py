"""
NumPy inference for the Spiral akili micro model.

The application runs the trained weights (```weights.npz```, created by
```python -m {{ app_label }}.core.akili.train```) with plain NumPy -- no torch, no
server. This file is the *runtime*: it re-implements the same forward pass
```train.py``` defines in torch, then decodes a plan greedily and
grammar-constrained. At every step the next byte must be legal plan DSL over
the currently active tools, so the model can never emit a malformed plan or
a tool outside the runtime injection (see ```dsl```).

If ```numba <https://numba.pydata.org>```_ is installed, the hot attention loop
is JIT-compiled automatically; without it the pure NumPy path runs the same
numbers (the model is matmul-dominated, so BLAS does the heavy lifting
either way).

### How a plan is decoded

1. The request bytes plus the ```SEP``` token run through the net once; the
    keys and values of every layer are kept (the KV cache).
2. The output logits rank all bytes; the ```Constrainer``` says which
    characters are legal at this point of the plan grammar -- the best
    legal one wins (greedy), so the result is deterministic.
3. The chosen byte runs as a single-position forward against the cache,
    yielding the next logits; repeat until the grammar allows the end.

The weights file carries its architecture as embedded metadata, read at
load time -- the runtime adapts to whatever ```train.py``` exported, so the
two can never disagree about dimensions.
"""

import json
import pathlib

import numpy

from {{ app_label }}.core.akili import tokenizer
from {{ app_label }}.core.akili.dsl import Constrainer

try:
    # optional acceleration: if numba is present the attention loop is
    # jit-compiled; if not, this fallback makes @njit a no-op decorator so
    # the pure-numpy path is the only dependency
    from numba import njit
except ImportError:  # pragma: no cover

    def njit(function=None, **kwargs):
        return function if function is not None else (lambda inner: inner)


# the trained weights live next to this module
WEIGHTS = pathlib.Path(__file__).parent / 'weights.npz'

# room reserved for the generated plan: the prompt is trimmed to leave at
# least this many positions free, and the decoder may emit up to this many
# characters. the longest plan the grammar can produce (a three-step
# relative chain) is 65 chars, so this carries headroom for both -- and the
# same constant is used by train.py, so training, evaluation, and inference
# all reserve identical room and can never disagree
PLAN_BUDGET = 80


@njit(cache=True)
def _attend(scores, values):  # pragma: no cover - numba compiles this
    # causal softmax-attention over one head, written as explicit loops so
    # numba can compile it; for each query row, softmax over the keys up to
    # and including itself (the causal cut at row+1), then take that weighted
    # average of the value vectors
    length = scores.shape[0]
    out = numpy.zeros_like(values)
    for row in range(length):
        # only positions 0..row are visible to query ```row``` (causality)
        line = scores[row, : row + 1]
        # numerically stable softmax: subtract the max before exp
        line = numpy.exp(line - line.max())
        line = line / line.sum()
        # weighted sum of the visible value vectors
        for col in range(row + 1):
            out[row] += line[col] * values[col]
    return out


class AkiliModel:
    """
    The trained micro model: weights, forward pass, constrained decoding.

    Construction loads ```weights.npz``` once: the named float32 arrays become
    the weight dict, and the embedded ```__config__``` json becomes the
    architecture the forward pass reads its dimensions from.

    """

    def __init__(self, path=None):
        bundle = numpy.load(path or WEIGHTS)
        # every named array except the metadata is a model parameter
        self._weights = {name: bundle[name] for name in bundle.files if name != '__config__'}
        # the metadata json carries dim/layers/heads/context plus provenance
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

        - **str**: A valid DSL line (or ```<nomatch>```)

        """
        context = self._config['context']
        # lowercase to match the training distribution, then keep the leading
        # context-PLAN_BUDGET bytes and append SEP: the model has learned
        # that planning begins right after SEP
        tokens = tokenizer.encode(request.lower())[: context - PLAN_BUDGET] + [tokenizer.SEP]
        # the grammar fence, restricted to the tools the runtime activated
        constrainer = Constrainer(active=active)
        # one empty key/value list per layer: the KV cache the prefill fills
        cache = [[] for _ in range(self._config['layers'])]
        # prefill: run the whole prompt once; this populates the cache and
        # returns the logits for the position right after SEP
        logits = self._forward(numpy.array(tokens, dtype=numpy.int64), cache)
        out = []
        for _ in range(PLAN_BUDGET):
            # ask the grammar what may come next at this point of the plan
            allowed = constrainer.allowed()
            # stop if the plan is complete (only EOS legal) or context is full
            if allowed == {'<eos>'} or len(tokens) >= context - 1:
                break
            # rank all byte ids by score, highest first (greedy)
            order = numpy.argsort(-logits)
            chosen = None
            for token in order:
                # the highest-ranked EOS, if ending is legal, stops the plan
                if token == tokenizer.EOS and '<eos>' in allowed:
                    chosen = None
                    break
                # otherwise take the highest-ranked byte the grammar permits;
                # this is where "smart" (the scores) meets "safe" (the fence)
                if token < 256 and chr(token) in allowed:
                    chosen = int(token)
                    break
            # no legal byte and no legal EOS: nothing more can be produced
            if chosen is None:
                break
            # commit the byte, tell the grammar, then advance the model one
            # position against the cache to get the next logits
            tokens.append(chosen)
            out.append(chosen)
            constrainer.feed(chr(chosen))
            logits = self._step(chosen, len(tokens) - 1, cache)
        # the decoded bytes are already only the plan (SEP was not appended
        # to ```out```); decode them back to the DSL line
        return tokenizer.decode(out) or ''

    def _forward(self, tokens, cache=None):
        # the full forward pass that primes the cache. it mirrors
        # AkiliNet.forward in train.py exactly, in numpy. matmul-dominated,
        # so BLAS does the heavy lifting
        weights = self._weights
        config = self._config
        # sum the byte meaning and the position meaning, just like training
        hidden = weights['embed.weight'][tokens] + weights['position.weight'][: len(tokens)]
        for layer in range(config['layers']):
            hidden = self._block(hidden, layer, cache)
        # final norm, then project the LAST position back to byte scores via
        # the tied embedding matrix (embed.weight.T is the head)
        hidden = _layer_norm(hidden, weights['ln.weight'], weights['ln.bias'])
        return hidden[-1] @ weights['embed.weight'].T

    def _step(self, token, position, cache):
        # the cheap path: push a SINGLE new token through, reusing the cached
        # keys and values for all earlier positions. this is what makes
        # decoding fast -- O(1) work per generated character instead of
        # re-running the whole sequence
        weights = self._weights
        config = self._config
        hidden = weights['embed.weight'][token] + weights['position.weight'][position]
        # shape it as a one-row sequence so the block code is shared
        hidden = hidden[None, :]
        for layer in range(config['layers']):
            hidden = self._block(hidden, layer, cache, step=True)
        hidden = _layer_norm(hidden, weights['ln.weight'], weights['ln.bias'])
        return hidden[-1] @ weights['embed.weight'].T

    def _block(self, hidden, layer, cache=None, step=False):
        # one transformer block, the numpy twin of train.py's Block:
        # pre-norm attention (residual), then pre-norm MLP (residual)
        weights = self._weights
        config = self._config
        prefix = f'blocks.{layer}.'
        # attention sublayer
        normed = _layer_norm(hidden, weights[prefix + 'ln1.weight'], weights[prefix + 'ln1.bias'])
        attended = self._attention(normed, prefix, config, None if cache is None else cache[layer], step)
        hidden = hidden + attended
        # MLP sublayer: linear -> gelu -> linear, added back as a residual
        normed = _layer_norm(hidden, weights[prefix + 'ln2.weight'], weights[prefix + 'ln2.bias'])
        inner = normed @ weights[prefix + 'mlp.0.weight'].T + weights[prefix + 'mlp.0.bias']
        inner = _gelu(inner)
        return hidden + inner @ weights[prefix + 'mlp.2.weight'].T + weights[prefix + 'mlp.2.bias']

    def _attention(self, normed, prefix, config, layer_cache, step):
        weights = self._weights
        heads = config['heads']
        dim = config['dim']
        # each head works in its own slice of the vector (dim // heads wide)
        size = dim // heads
        # one fused projection produces query, key, value, then split in 3
        qkv = normed @ weights[prefix + 'attn.in_proj_weight'].T + weights[prefix + 'attn.in_proj_bias']
        query, key, value = numpy.split(qkv, 3, axis=-1)
        # the KV cache: in a single-token step, append this token's key/value
        # to everything seen so far, so the new query can attend over the
        # whole left context without recomputing it
        if layer_cache is not None:
            if step:
                key = numpy.concatenate([layer_cache[0], key])
                value = numpy.concatenate([layer_cache[1], value])
            layer_cache[:] = [key, value]
        out = numpy.empty_like(query)
        for head in range(heads):
            # this head's slice of the q/k/v vectors
            sliced = slice(head * size, (head + 1) * size)
            # scaled dot-product scores: query . key / sqrt(head_size); the
            # scaling keeps the softmax from saturating as size grows
            scores = (query[:, sliced] @ key[:, sliced].T) / numpy.sqrt(size)
            if step:
                # one query over all cached keys: a single softmax row, no
                # causal loop needed (the cache only holds left context)
                line = numpy.exp(scores[0] - scores[0].max())
                out[:, sliced] = (line / line.sum()) @ value[:, sliced]
            else:
                # prefill: the causal, per-row attention over the prompt
                out[:, sliced] = _attend(scores.astype(numpy.float32), value[:, sliced].astype(numpy.float32))
        # recombine the heads through the output projection
        return out @ weights[prefix + 'attn.out_proj.weight'].T + weights[prefix + 'attn.out_proj.bias']


def _layer_norm(x, gain, bias):
    # normalize each vector to zero mean and unit variance, then scale and
    # shift by the learned gain/bias; the 1e-5 guards against divide-by-zero
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / numpy.sqrt(var + 1e-5) * gain + bias


def _gelu(x):
    # the tanh approximation of GELU, matching torch's nn.GELU default well
    # enough for inference; a smooth gate that lets the MLP express
    # nonlinear combinations of features
    return 0.5 * x * (1.0 + numpy.tanh(numpy.sqrt(2.0 / numpy.pi) * (x + 0.044715 * x**3)))
