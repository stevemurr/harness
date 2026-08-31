Rename the method `apply` on the `Step` class to `transform`, everywhere in this package --
the base class, every subclass, and every call site. Nothing may still call `.apply(`.
Update the tests too if they need it, and run the suite until it passes.
