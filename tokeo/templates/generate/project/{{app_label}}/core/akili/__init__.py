"""
The Spiral akili micro language model -- a small, complete, teachable lab.

This package is a from-scratch decoder-only transformer that does exactly
one thing: turn a calendar request into a plan of tool calls. It is meant
to be read end to end. The modules, in the order they make sense:

- ``dsl`` -- the plan language: how a plan is written, parsed, and (most
    importantly) *constrained*, so the model can only emit legal plans
- ``tokenizer`` -- the byte vocabulary (no learned subwords), the reason
    the model copies dates and numbers exactly
- ``data`` -- the synthetic data generator and its single source of
    language, ``AKILI-LEX.yaml``
- ``train`` -- the network and the training loop (the only torch here)
- ``infer`` -- the same forward pass in plain NumPy, plus the
    grammar-constrained greedy decoder used at runtime

The long-form walkthrough below (``AKILI-LLM.md``) explains the training
pipeline, the anatomy of the weights, and constrained decoding with
diagrams.

.. include:: ./AKILI-LLM.md
"""
