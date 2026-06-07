"""
Training for the {{ app_name }} fundi micro model.

A from-scratch decoder-only transformer (a few hundred thousand parameters)
learns one mapping: request bytes -> plan DSL bytes. Torch is a training-side
tool only; the application runs the trained weights with plain NumPy (see
``infer.py``). Run ``python -m {{ app_label }}.app.fundi.train`` from the project
root; the weights land in ``{{ app_label }}/app/fundi/weights.npz``.
"""

import json
import os
import pathlib

import numpy
import torch
from torch import nn

from {{ app_label }}.app.fundi import tokenizer
from {{ app_label }}.app.fundi.data import dataset

# small enough to train on a laptop cpu, big enough for the closed domain
CONFIG = dict(dim=96, layers=3, heads=4, ff=384, context=184, vocab=tokenizer.VOCAB)


class Block(nn.Module):
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
    tokens = tokenizer.encode(request)[: context - 60] + [tokenizer.SEP] + tokenizer.encode(dsl) + [tokenizer.EOS]
    return tokens[:context]


def tensorize(examples, context):
    rows = []
    for request, dsl in examples:
        tokens = encode_pair(request, dsl, context)
        rows.append(tokens + [tokenizer.PAD] * (context - len(tokens)))
    return torch.tensor(rows, dtype=torch.long)


def main():
    torch.manual_seed(7)
    torch.set_num_threads(os.cpu_count() or 1)
    steps = int(os.environ.get('FUNDI_STEPS', '1400'))
    batch = int(os.environ.get('FUNDI_BATCH', '96'))
    examples = dataset(int(os.environ.get('FUNDI_DATA', '30000')))
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
    save(model, accuracy)


@torch.no_grad()
def evaluate(model, examples):
    model.eval()
    hits = 0
    for request, dsl in examples:
        if generate(model, request) == dsl:
            hits += 1
    model.train()
    return hits / len(examples)


@torch.no_grad()
def generate(model, request):
    tokens = tokenizer.encode(request)[: CONFIG['context'] - 60] + [tokenizer.SEP]
    for _ in range(58):
        logits = model(torch.tensor([tokens]))[0, -1]
        token = int(logits.argmax())
        if token == tokenizer.EOS or len(tokens) >= CONFIG['context'] - 1:
            break
        tokens.append(token)
    return tokenizer.decode(tokens[tokens.index(tokenizer.SEP) + 1:])


def save(model, accuracy):
    weights = {name: parameter.detach().numpy().astype(numpy.float32) for name, parameter in model.state_dict().items()}
    weights['__config__'] = numpy.frombuffer(json.dumps(CONFIG | {'accuracy': round(accuracy, 4)}).encode(), dtype=numpy.uint8)
    target = pathlib.Path(__file__).parent / 'weights.npz'
    numpy.savez_compressed(target, **weights)
    print('saved:', target, f'({target.stat().st_size / 1e6:.2f} MB)')


if __name__ == '__main__':
    main()
