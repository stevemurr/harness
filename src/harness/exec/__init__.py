"""Starting things, watching them, and stopping all of them.

The first package pulled out of a flat `harness/`. It holds the three concerns that were
tangled together in one 458-line module: the OS-level fact that a process has descendants
(`spawn`), the table of what this run started (`processes`), and reading a child's output as
it appears (`monitor`).
"""
