"""
Training for the {{ app_name }} fundi micro model.

A from-scratch decoder-only transformer (a few hundred thousand parameters)
learns one mapping: request bytes -> plan DSL bytes. Torch is a training-side
tool only; the application runs the trained weights with plain NumPy (see
``infer.py``). Run ``python -m {{ app_label }}.core.fundi.train`` from the project
root; the weights land in ``{{ app_label }}/core/fundi/weights.npz``.

### The pipeline, step by step

1. ``data.dataset()`` generates the synthetic (request, plan) pairs; the
    first 600 are held back for the final evaluation and never trained on.
2. ``encode_pair``/``tensorize`` turn every pair into one fixed-length row
    of byte tokens: ``request + SEP + plan + EOS``, padded with ``PAD``.
3. The loop samples random batches and minimizes cross-entropy on the
    next-token prediction -- but only on the plan side: every position
    before ``SEP`` is masked out, so the model is never graded on
    predicting the request, only on producing the plan.
4. ``evaluate`` decodes the held-out requests greedily and counts exact
    plan-line matches -- the number printed as accuracy.
5. ``save`` exports every parameter as a named float32 array into
    ``weights.npz``, together with the architecture, so the NumPy runtime
    can never load weights that do not fit its math.

### Command line

- ``--no-minus``: train without the signed-offset wordings (minus/ago,
    minus/vor) -- an ablation switch: same architecture and budget, but
    the resulting model has no notion of minus days. Keep the flag
    identical across resumed chunks (``FUNDI_CKPT``), since the dataset
    and its held-out split are rebuilt from seed and flags on every
    invocation.

### Environment knobs

- ``FUNDI_STEPS`` (default 1400): optimizer steps of the full schedule
- ``FUNDI_BATCH`` (default 96): examples per step
- ``FUNDI_DATA`` (default 30000): generated examples (600 held out)
- ``FUNDI_CKPT``: path to a checkpoint file; enables resumable training
- ``FUNDI_CHUNK``: steps per invocation when checkpointing (call the
    module repeatedly until the full schedule is done)
"""

import argparse
import json
import os
import pathlib

import numpy
import torch
from torch import nn

from {{ app_label }}.core.fundi import tokenizer
from {{ app_label }}.core.fundi.data import dataset

# small enough to train on a laptop cpu, big enough for the closed domain
CONFIG = dict(dim=96, layers=3, heads=4, ff=384, context=184, vocab=tokenizer.VOCAB)


class Block(nn.Module):
    """
    One transformer block: attention, then a small per-position MLP.

    Both halves are residual (their output is added to the input), with a
    layer norm in front of each -- the standard pre-norm layout that keeps
    tiny models stable in training.

    """

    def __init__(self, dim, heads, ff):
        super().__init__()
        self.ln1 = nn.LayerNorm(dim)
        self.attn = nn.MultiheadAttention(dim, heads, batch_first=True)
        self.ln2 = nn.LayerNorm(dim)
        self.mlp = nn.Sequential(nn.Linear(dim, ff), nn.GELU(), nn.Linear(ff, dim))

    def forward(self, hidden, mask):
        normed = self.ln1(hidden)
        attended, _ = self.attn(normed, normed, normed, attn_mask=mask, need_weights=False)
        hidden = hidden + attended
        return hidden + self.mlp(self.ln2(hidden))


class FundiNet(nn.Module):
    """
    The decoder-only transformer behind fundi.

    Byte embedding plus learned position embedding, ``config['layers']``
    blocks, a final layer norm, and a head projecting back to byte logits.
    The head shares its matrix with the embedding (tied weights): the same
    table maps bytes to vectors and vectors back to byte scores, which
    halves the parameter count of the largest layer. The causal mask makes
    every position see only its left context, so the net can generate one
    byte at a time.

    """

    def __init__(self, config):
        super().__init__()
        self.embed = nn.Embedding(config['vocab'], config['dim'])
        self.position = nn.Embedding(config['context'], config['dim'])
        self.blocks = nn.ModuleList(Block(config['dim'], config['heads'], config['ff']) for _ in range(config['layers']))
        self.ln = nn.LayerNorm(config['dim'])
        self.head = nn.Linear(config['dim'], config['vocab'], bias=False)
        self.head.weight = self.embed.weight   # tied embeddings keep it small
        # small init keeps the tied logits in a sane range from step one
        nn.init.normal_(self.embed.weight, std=0.02)
        nn.init.normal_(self.position.weight, std=0.02)

    def forward(self, tokens):
        length = tokens.shape[1]
        mask = torch.triu(torch.full((length, length), float('-inf')), diagonal=1)
        hidden = self.embed(tokens) + self.position(torch.arange(length))
        for block in self.blocks:
            hidden = block(hidden, mask)
        return self.head(self.ln(hidden))


def encode_pair(request, dsl, context):
    """
    Encode one training example as ``request + SEP + plan + EOS`` tokens.

    The request is clipped so at least 60 token positions stay free for
    the plan; the whole row is clipped to the context length.

    """
    tokens = tokenizer.encode(request)[: context - 60] + [tokenizer.SEP] + tokenizer.encode(dsl) + [tokenizer.EOS]
    return tokens[:context]


def tensorize(examples, context):
    """Pad every encoded example to one fixed-length tensor row."""
    rows = []
    for request, dsl in examples:
        tokens = encode_pair(request, dsl, context)
        rows.append(tokens + [tokenizer.PAD] * (context - len(tokens)))
    return torch.tensor(rows, dtype=torch.long)


def main():
    """
    Run the full training schedule and export ``weights.npz``.

    The ``--no-minus`` command line switch builds the ablation dataset
    without signed day offsets; the choice is recorded in the exported
    metadata, so a weights file always tells what it was taught.

    Deterministic by construction: fixed seeds for torch and the data
    generator, so the same invocation reproduces the same weights. With
    ``FUNDI_CKPT`` set, the run resumes from the stored step and stops
    after ``FUNDI_CHUNK`` steps, saving model, optimizer, schedule, and
    rng state -- evaluation and export only happen once the final step of
    the schedule is reached.

    """
    torch.manual_seed(7)
    torch.set_num_threads(os.cpu_count() or 1)
    steps = int(os.environ.get('FUNDI_STEPS', '1400'))
    batch = int(os.environ.get('FUNDI_BATCH', '96'))
    parser = argparse.ArgumentParser(description='train the fundi micro language model')
    parser.add_argument(
        '--no-minus',
        action='store_true',
        help='train without signed day offsets (minus/ago wordings)',
    )
    arguments = parser.parse_args()
    minus = not arguments.no_minus
    examples = dataset(int(os.environ.get('FUNDI_DATA', '30000')), minus=minus)
    held = examples[:600]
    train = examples[600:]
    data = tensorize(train, CONFIG['context'])
    model = FundiNet(CONFIG)
    print('parameters:', sum(parameter.numel() for parameter in model.parameters()))
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=3e-3, total_steps=steps, pct_start=0.05)
    loss_fn = nn.CrossEntropyLoss(ignore_index=tokenizer.PAD)
    # optional chunked training: a checkpoint lets short runs resume, so
    # the full schedule can be split over several invocations
    checkpoint = os.environ.get('FUNDI_CKPT')
    first = 0
    if checkpoint and pathlib.Path(checkpoint).exists():
        state = torch.load(checkpoint, weights_only=False)
        model.load_state_dict(state['model'])
        optimizer.load_state_dict(state['optimizer'])
        schedule.load_state_dict(state['schedule'])
        torch.set_rng_state(state['rng'])
        first = state['step']
        print('resumed at step', first)
    chunk = int(os.environ.get('FUNDI_CHUNK', str(steps)))
    last = min(first + chunk, steps)
    for step in range(first, last):
        rows = data[torch.randint(0, data.shape[0], (batch,))]
        logits = model(rows[:, :-1])
        # the loss only counts the plan side: everything before SEP is input
        targets = rows[:, 1:].clone()
        seen_sep = (rows[:, :-1] == tokenizer.SEP).cumsum(dim=1) > 0
        targets[~seen_sep] = tokenizer.PAD
        loss = loss_fn(logits.reshape(-1, CONFIG['vocab']), targets.reshape(-1))
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        schedule.step()
        if step % 50 == 0 or step == steps - 1:
            print(f'step {step:5d}  loss {loss.item():.4f}', flush=True)
    if checkpoint:
        torch.save({'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                    'schedule': schedule.state_dict(), 'rng': torch.get_rng_state(), 'step': last}, checkpoint)
        print('checkpointed at step', last)
    if last < steps:
        return
    accuracy = evaluate(model, held)
    print(f'held-out exact-plan accuracy: {accuracy:.3f}')
    save(model, accuracy, minus)


@torch.no_grad()
def evaluate(model, examples):
    """
    Exact-plan accuracy on held-out examples.

    Greedy decoding without the grammar automaton: the raw model must
    produce the plan line character by character, byte-exact. The runtime
    adds the grammar constraint on top, so real use is at least this good.

    """
    model.eval()
    hits = 0
    for request, dsl in examples:
        if generate(model, request) == dsl:
            hits += 1
    model.train()
    return hits / len(examples)


@torch.no_grad()
def generate(model, request):
    """Greedy-decode the plan line for one request (training-side only)."""
    tokens = tokenizer.encode(request)[: CONFIG['context'] - 60] + [tokenizer.SEP]
    for _ in range(58):
        logits = model(torch.tensor([tokens]))[0, -1]
        token = int(logits.argmax())
        if token == tokenizer.EOS or len(tokens) >= CONFIG['context'] - 1:
            break
        tokens.append(token)
    return tokenizer.decode(tokens[tokens.index(tokenizer.SEP) + 1:])


def save(model, accuracy, minus=True):
    """
    Export the trained model as ``weights.npz``.

    One named float32 array per parameter (the names follow the torch
    state dict, e.g. ``blocks.0.attn.in_proj_weight``), plus the
    architecture and the achieved held-out accuracy as embedded json under
    ``__config__`` -- the NumPy runtime reads its dimensions from there,
    so inference and weights can never drift apart.

    """
    weights = {name: parameter.detach().numpy().astype(numpy.float32) for name, parameter in model.state_dict().items()}
    weights['__config__'] = numpy.frombuffer(
        json.dumps(CONFIG | {'accuracy': round(accuracy, 4), 'minus': minus}).encode(),
        dtype=numpy.uint8,
    )
    target = pathlib.Path(__file__).parent / 'weights.npz'
    numpy.savez_compressed(target, **weights)
    print('saved:', target, f'({target.stat().st_size / 1e6:.2f} MB)')


if __name__ == '__main__':
    main()
