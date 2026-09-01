"""Worker handlers.

A handler is `(task, ctx) -> Verdict` (a regular function or a coroutine).
A slot in the config points to one by the string "module:function".
"""
