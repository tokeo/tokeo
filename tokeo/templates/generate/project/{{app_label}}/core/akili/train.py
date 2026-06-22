# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# This file is part of the Tokeo-Fundi project.
#
# The akili module is primarily a training and demonstration tool,
# intended for experimentation and the gathering of insights.
# It serves as foundational material within this context.
#
# IMPORTANT: While the surrounding repository may be licensed under the
# MIT License, this specific file is governed exclusively by the
# Tokeo-Fundi Source-Available License 1.0.
#
# Use, modification, and distribution are permitted strictly in
# accordance with the terms of this license, which includes specific
# revenue and headcount thresholds for zero-cost qualification.
#
# A copy of the full license is available at:
# https://github.com/tokeo/fundi/blob/master/LICENSE.md
#
# If your entity does not qualify for the zero-cost license, a separate
# commercial Enterprise License is required.

"""
Training for the Spiral akili micro model.

A from-scratch decoder-only transformer (a few hundred thousand parameters)
learns one mapping: request bytes -> plan DSL bytes. Torch is a training-side
tool only; the application runs the trained weights with plain NumPy (see
```infer.py```). Run ```python -m {{ app_label }}.core.akili.train``` from the project
root; the weights land in ```{{ app_label }}/core/akili/weights.npz```.

### The pipeline, step by step

1. ```data.dataset()``` generates the synthetic (request, plan) pairs; the
    first 600 are held back for the final evaluation and never trained on.
2. ```encode_pair```/```tensorize``` turn every pair into one fixed-length row
    of byte tokens: ```request + SEP + plan + EOS```, padded with ```PAD```.
3. The loop samples random batches and minimizes cross-entropy on the
    next-token prediction -- but only on the plan side: every position
    before ```SEP``` is masked out, so the model is never graded on
    predicting the request, only on producing the plan.
4. ```evaluate``` decodes the held-out requests greedily and counts exact
    plan-line matches -- the number printed as accuracy.
5. ```save``` exports every parameter as a named float32 array into
    ```weights.npz```, together with the architecture, so the NumPy runtime
    can never load weights that do not fit its math.

### Command line

- ```--no-minus```: train without the signed-offset wordings (minus/ago,
    minus/vor) -- an ablation switch: same architecture and budget, but
    the resulting model has no notion of minus days. Keep the flag
    identical across resumed chunks (```AKILI_CKPT```), since the dataset
    and its held-out split are rebuilt from seed and flags on every
    invocation.

### Environment knobs

- ```AKILI_STEPS``` (default 3000): optimizer steps of the full schedule
- ```AKILI_BATCH``` (default 96): examples per step
- ```AKILI_DATA``` (default 40000): generated examples (600 held out)
- ```AKILI_CKPT```: path to a checkpoint file; enables resumable training
- ```AKILI_CHUNK```: steps per invocation when checkpointing (call the
    module repeatedly until the full schedule is done)
"""

import argparse
import json
import os
import pathlib

import numpy
import torch
from torch import nn

from {{ app_label }}.core.akili import tokenizer
from {{ app_label }}.core.akili.data import dataset
# one source of truth for the plan-length budget: the same constant the
# NumPy runtime uses, so training-side decoding and evaluation reserve
# exactly as much room for the plan as inference does
from {{ app_label }}.core.akili.infer import PLAN_BUDGET

# the architecture, fixed and small. every value here is a lever on
# capacity and parameter count; the comments say what each one buys:
# - dim: the width of every token vector. more width = more capacity per
#   token, but block parameters grow roughly with dim squared
# - layers: depth. more blocks = more rounds of "look around and combine",
#   which is what lets a three-step plan be reasoned about
# - heads: parallel attention channels; each looks at the sequence a
#   different way (here 96/4 = 24 dimensions per head)
# - ff: the per-position MLP's hidden width, the usual 4x dim
# - context: the maximum number of token positions; it caps request+plan
#   length and is the size of the position embedding table
# - vocab: 259, fixed by the tokenizer (256 bytes + PAD/SEP/EOS)
CONFIG = dict(dim=128, layers=3, heads=4, ff=512, context=184, vocab=tokenizer.VOCAB)


class Block(nn.Module):
    """
    One transformer block: attention, then a small per-position MLP.

    Both halves are *residual* (their output is added back to the input),
    and each is preceded by a layer norm -- the pre-norm layout. Two reasons
    this matters for a tiny model: the residual add gives gradients a short
    path back to early layers (so depth trains at all), and normalizing the
    input to each sublayer keeps activations in a stable range, which lets
    the fairly high learning rate below converge without diverging.

    """

    def __init__(self, dim, heads, ff):
        super().__init__()
        # normalize, then attend: every position mixes in information from
        # the positions it is allowed to see (its left context)
        self.ln1 = nn.LayerNorm(dim)
        self.attn = nn.MultiheadAttention(dim, heads, batch_first=True)
        # normalize, then transform each position on its own; GELU is the
        # smooth nonlinearity that gives the MLP its expressive power
        self.ln2 = nn.LayerNorm(dim)
        self.mlp = nn.Sequential(nn.Linear(dim, ff), nn.GELU(), nn.Linear(ff, dim))

    def forward(self, hidden, mask):
        # pre-norm attention sublayer, added back as a residual
        normed = self.ln1(hidden)
        attended, _ = self.attn(normed, normed, normed, attn_mask=mask, need_weights=False)
        hidden = hidden + attended
        # pre-norm MLP sublayer, also residual; the return is the block output
        return hidden + self.mlp(self.ln2(hidden))


class AkiliNet(nn.Module):
    """
    The decoder-only transformer behind akili.

    A byte embedding plus a learned position embedding feed a stack of
    blocks, a final layer norm, and a head that projects each position back
    to ```vocab``` byte scores. The head *shares* its matrix with the
    embedding (tied weights): the one table both maps a byte id to a vector
    and maps a vector back to byte scores. That halves the parameters of the
    single largest layer and ties "what a byte means" to "what predicts that
    byte", which helps a small model. A causal mask makes each position
    attend only to itself and the positions to its left, which is exactly
    what lets the trained net generate one byte at a time.

    """

    def __init__(self, config):
        super().__init__()
        # row v of this table is the learned meaning of byte id v
        self.embed = nn.Embedding(config['vocab'], config['dim'])
        # row p is the learned meaning of "being at position p in the line"
        self.position = nn.Embedding(config['context'], config['dim'])
        # the stack of transformer blocks (depth = config['layers'])
        self.blocks = nn.ModuleList(Block(config['dim'], config['heads'], config['ff']) for _ in range(config['layers']))
        # a final normalization before scoring stabilizes the logits
        self.ln = nn.LayerNorm(config['dim'])
        # the output head: vector -> one score per byte id, no bias
        self.head = nn.Linear(config['dim'], config['vocab'], bias=False)
        # tie the head to the embedding: same matrix, used both directions
        self.head.weight = self.embed.weight
        # a small initial spread keeps the (tied) logits sane from step one;
        # 0.02 is the conventional transformer init standard deviation
        nn.init.normal_(self.embed.weight, std=0.02)
        nn.init.normal_(self.position.weight, std=0.02)

    def forward(self, tokens):
        length = tokens.shape[1]
        # the causal mask: an upper-triangular matrix of -inf above the
        # diagonal. added to the attention scores, it makes attending to any
        # position to the *right* impossible (softmax of -inf is 0), so the
        # model can only use left context -- the basis of left-to-right
        # generation
        mask = torch.triu(torch.full((length, length), float('-inf')), diagonal=1)
        # the input to the blocks is the sum of what each byte means and
        # where it sits: embed(token) + position(index)
        hidden = self.embed(tokens) + self.position(torch.arange(length))
        for block in self.blocks:
            hidden = block(hidden, mask)
        # normalize, then score every position back into byte space
        return self.head(self.ln(hidden))


def encode_pair(request, dsl, context):
    """
    Encode one training example as ```request + SEP + plan + EOS``` tokens.

    The request is clipped so at least ```PLAN_BUDGET``` token positions stay
    free for the plan, matching exactly what the runtime reserves; the whole
    row is then clipped to the context length as a final guard.

    ### Args

    - **request** (str): The user request text
    - **dsl** (str): The target plan line
    - **context** (int): The fixed sequence length

    ### Returns

    - **list**: The token ids for this example (before padding)

    """
    # layout: request bytes, the SEP boundary, the plan bytes, then EOS.
    # the request keeps its leading context-PLAN_BUDGET characters, leaving
    # room for the plan; tokens[:context] is the final hard cap
    tokens = tokenizer.encode(request)[: context - PLAN_BUDGET] + [tokenizer.SEP] + tokenizer.encode(dsl) + [tokenizer.EOS]
    return tokens[:context]


def tensorize(examples, context):
    """
    Pad every encoded example to one fixed-length tensor of rows.

    ### Args

    - **examples** (list): (request, dsl) pairs
    - **context** (int): The fixed row length

    ### Returns

    - **torch.Tensor**: A ```(len(examples), context)``` long tensor

    """
    rows = []
    for request, dsl in examples:
        tokens = encode_pair(request, dsl, context)
        # pad the row out to the fixed width with PAD; the loss ignores PAD,
        # so padding never contributes a gradient
        rows.append(tokens + [tokenizer.PAD] * (context - len(tokens)))
    return torch.tensor(rows, dtype=torch.long)


def main():
    """
    Run the full training schedule and export ```weights.npz```.

    The ```--no-minus``` switch builds the ablation dataset without signed day
    offsets; the choice is recorded in the exported metadata, so a weights
    file always tells what it was taught. Deterministic by construction:
    fixed seeds for torch and the data generator reproduce the same weights
    from the same invocation. With ```AKILI_CKPT``` set, the run resumes from
    the stored step and stops after ```AKILI_CHUNK``` steps, saving model,
    optimizer, schedule, and rng state; evaluation and export happen only
    once the final step of the schedule is reached.

    """
    # fixed seed + a single deterministic thread count make the run
    # reproducible: same flags in, same weights out
    torch.manual_seed(7)
    torch.set_num_threads(os.cpu_count() or 1)
    steps = int(os.environ.get('AKILI_STEPS', '4000'))
    batch = int(os.environ.get('AKILI_BATCH', '96'))
    parser = argparse.ArgumentParser(description='train the akili micro language model')
    parser.add_argument(
        '--no-minus',
        action='store_true',
        help='train without signed day offsets (minus/ago wordings)',
    )
    arguments = parser.parse_args()
    # minus=True is the normal model; --no-minus flips it for the ablation
    minus = not arguments.no_minus
    # build the dataset, then split off a fixed held-out slice that the loop
    # never sees -- the only honest place to read accuracy from
    examples = dataset(int(os.environ.get('AKILI_DATA', '60000')), minus=minus)
    held = examples[:600]
    train = examples[600:]
    data = tensorize(train, CONFIG['context'])
    model = AkiliNet(CONFIG)
    # the printed number is the total parameter count (~378k by default)
    print('parameters:', sum(parameter.numel() for parameter in model.parameters()))
    # AdamW: adaptive per-parameter steps with weight decay (0.01) as gentle
    # regularization. the learning rate 3e-3 is high for a transformer, which
    # the OneCycle schedule below makes safe: warm up over the first 5% of
    # steps, then anneal down -- fast, stable convergence for a short run
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=3e-3, total_steps=steps, pct_start=0.05)
    # cross-entropy on the next byte; ignore_index=PAD means padded target
    # positions contribute no loss and no gradient
    loss_fn = nn.CrossEntropyLoss(ignore_index=tokenizer.PAD)
    # optional chunked training: a checkpoint lets short runs resume, so the
    # full schedule can be split over several invocations (laptop-friendly)
    checkpoint = os.environ.get('AKILI_CKPT')
    first = 0
    if checkpoint and pathlib.Path(checkpoint).exists():
        # restore everything that defines "where we were": weights, the
        # optimizer's momentum, the schedule's position, and the rng state
        state = torch.load(checkpoint, weights_only=False)
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        schedule.load_state_dict(state['schedule'])
        torch.set_rng_state(state['rng'])
        first = state['step']
        print('resumed at step', first)
    chunk = int(os.environ.get('AKILI_CHUNK', str(steps)))
    last = min(first + chunk, steps)
    for step in range(first, last):
        # draw a random batch of rows (sampling with replacement)
        rows = data[torch.randint(0, data.shape[0], (batch,))]
        # predict the next token at every position from the ones before it;
        # the input is all positions but the last, the target is shifted by
        # one (so position i predicts what actually sits at i+1)
        logits = model(rows[:, :-1])
        targets = rows[:, 1:].clone()
        # the masked-loss trick, the core of teaching only the plan: mark
        # every position from the first SEP onward as "seen_sep". positions
        # NOT yet past SEP (the request side) get their target set to PAD,
        # which the loss ignores -- so the model is graded only on bytes it
        # must *produce* (the plan), never on copying back the request
        seen_sep = (rows[:, :-1] == tokenizer.SEP).cumsum(dim=1) > 0
        targets[~seen_sep] = tokenizer.PAD
        loss = loss_fn(logits.reshape(-1, CONFIG['vocab']), targets.reshape(-1))
        # the standard optimizer step: clear old gradients, backpropagate,
        # clip the gradient norm to 1.0 (a guard against rare exploding
        # gradients that would otherwise wreck a tiny model), then step the
        # weights and advance the learning-rate schedule
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        schedule.step()
        if step % 50 == 0 or step == steps - 1:
            print(f'step {step:5d}  loss {loss.item():.4f}', flush=True)
    if checkpoint:
        # persist enough to resume exactly where this chunk stopped
        torch.save(
            {
                'model': model.state_dict(),
                'optimizer': optimizer.state_dict(),
                'schedule': schedule.state_dict(),
                'rng': torch.get_rng_state(),
                'step': last,
            },
            checkpoint,
        )
        print('checkpointed at step', last)
    # when chunking, only the invocation that reaches the final step goes on
    # to evaluate and export; earlier chunks just save and return
    if last < steps:
        return
    accuracy = evaluate(model, held)
    print(f'held-out exact-plan accuracy: {accuracy:.3f}')
    save(model, accuracy, minus)


@torch.no_grad()
def evaluate(model, examples):
    """
    Exact-plan accuracy on held-out examples.

    Greedy decoding *without* the grammar automaton: the raw model has to
    produce the plan line character by character, byte-exact, with no fence
    to catch a wrong turn. That makes this number an honest lower bound --
    the runtime adds the grammar on top, so real use is at least this good.

    ### Args

    - **model** (AkiliNet): The trained model in eval mode
    - **examples** (list): Held-out (request, dsl) pairs

    ### Returns

    - **float**: Fraction of requests whose decoded plan matches exactly

    """
    model.eval()
    hits = 0
    for request, dsl in examples:
        # a hit is a *byte-exact* match of the whole plan line; anything off
        # -- a wrong tool, a miscopied digit, a truncated step -- is a miss
        if generate(model, request) == dsl:
            hits += 1
    model.train()
    return hits / len(examples)


@torch.no_grad()
def generate(model, request):
    """
    Greedy-decode the plan line for one request (training-side only).

    This mirrors the runtime decoder in ```infer.py``` but without the grammar
    constraint and without the KV cache (clarity over speed -- it is only
    used for evaluation). The same ```PLAN_BUDGET``` bounds it, so a long plan
    (a relative chain) is decoded in full instead of being cut off and
    wrongly counted as a miss.

    ### Args

    - **model** (AkiliNet): The trained model
    - **request** (str): The user request

    ### Returns

    - **str**: The decoded plan line (the bytes after SEP)

    """
    tokens = tokenizer.encode(request)[: CONFIG['context'] - PLAN_BUDGET] + [tokenizer.SEP]
    for _ in range(PLAN_BUDGET):
        # re-run the whole sequence and read the scores at the last position;
        # argmax is the greedy choice (no sampling -> deterministic)
        logits = model(torch.tensor([tokens]))[0, -1]
        token = int(logits.argmax())
        # stop at EOS or when the context is full
        if token == tokenizer.EOS or len(tokens) >= CONFIG['context'] - 1:
            break
        tokens.append(token)
    # return only the plan side: the bytes after the SEP boundary
    return tokenizer.decode(tokens[tokens.index(tokenizer.SEP) + 1 :])  # noqa E203


def save(model, accuracy, minus=True):
    """
    Export the trained model as ```weights.npz```.

    One named float32 array per parameter (the names follow the torch state
    dict, e.g. ```blocks.0.attn.in_proj_weight```), plus the architecture and
    the achieved held-out accuracy as embedded json under ```__config__```.
    The NumPy runtime reads its dimensions from that json, so inference and
    weights can never drift apart: a weights file carries its own shape.

    ### Args

    - **model** (AkiliNet): The trained model
    - **accuracy** (float): The held-out exact-plan accuracy to record
    - **minus** (bool): Whether minus wordings were taught (recorded)

    """
    # every parameter becomes a named float32 array (float32 keeps the file
    # ~1.5 MB and is all the NumPy forward pass needs)
    weights = {name: parameter.detach().numpy().astype(numpy.float32) for name, parameter in model.state_dict().items()}
    # the architecture + provenance ride along as json bytes under a reserved
    # name, so loading can rebuild the exact math and report what it was
    weights['__config__'] = numpy.frombuffer(
        json.dumps(CONFIG | {'accuracy': round(accuracy, 4), 'minus': minus}).encode(),
        dtype=numpy.uint8,
    )
    target = pathlib.Path(__file__).parent / 'weights.npz'
    numpy.savez_compressed(target, **weights)
    print('saved:', target, f'({target.stat().st_size / 1e6:.2f} MB)')


if __name__ == '__main__':
    main()
