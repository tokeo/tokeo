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
The {{ app_name }} akili micro language model -- a small, complete,
teachable lab.

This package is a from-scratch decoder-only transformer that does exactly
one thing: turn a calendar request into a plan of tool calls. It is meant
to be read end to end. The modules, in the order they make sense:

- ```dsl``` -- the plan language: how a plan is written, parsed, and (most
    importantly) *constrained*, so the model can only emit legal plans
- ```tokenizer``` -- the byte vocabulary (no learned subwords), the reason
    the model copies dates and numbers exactly
- ```data``` -- the synthetic data generator and its single source of
    language, ```AKILI-LEX.yaml```
- ```train``` -- the network and the training loop (the only torch here)
- ```infer``` -- the same forward pass in plain NumPy, plus the
    grammar-constrained greedy decoder used at runtime

The long-form walkthrough below (```AKILI-LLM.md```) explains the training
pipeline, the anatomy of the weights, and constrained decoding with
diagrams. After it, ```AKILI-USE.md``` is the guided demo in three acts:
the fundi agent, the akili model, and -- on purpose -- the limits where
a language model breaks.

.. include:: ./AKILI-LLM.md

.. include:: ./AKILI-USE.md
"""
