"""Exception types for the template registry, in a module that is never reloaded.

This file exists because of one property of hot-reloading: ``importlib.reload``
creates **new class objects**. A caller that did ``except TemplateError`` holds the
class from before the reload, a freshly reloaded template raises the one from after,
and the two are different objects — so the ``except`` does not fire. A tidy
validation message becomes an unhandled exception, and only for the first call after
an edit, which is the hardest kind of bug to catch anyone in the act of.

So the classes live here and every template imports them. ``refresh()`` reloads each
template module and the registry, and skips this one, which keeps their identity
stable across as many edits as you like.

The cost, and it is worth stating: **adding an exception class here needs a restart**
of the server to take effect, because this module is deliberately never reloaded.
"""

from __future__ import annotations

__all__ = ["TemplateError", "TemplateReloadError", "UnknownTemplateError"]


class TemplateError(ValueError):
    """A template's parameters cannot produce valid geometry.

    The message always names the offending value. It is an API: for an MCP client it
    is the model's only self-correction signal, so it says which parameter is wrong
    and, where there is one, the value that would work.
    """


class UnknownTemplateError(KeyError):
    """No template registered under that name."""


class TemplateReloadError(RuntimeError):
    """A template's source changed on disk and would not import."""
