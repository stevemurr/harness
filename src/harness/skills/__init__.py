"""The skills this harness ships, one folder each; see `harness.state.skills`.

A package so `importlib.resources` can find the folder from an installed wheel. There is
no Python here on purpose: a skill is a document, and these are read the way a project's
own are, which means a project can replace any of them by name.
"""
