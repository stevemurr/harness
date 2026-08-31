Rename the method `apply` on the `Step` class to `transform` -- the base class, every
subclass, and every call site.

There is also a `Discount` class in `pipeline/pricing.py` with its own `apply` method. It has
nothing to do with `Step` and must keep the name `apply`, along with everything that calls
it. Update the tests where they need it, and run the suite until it passes.
