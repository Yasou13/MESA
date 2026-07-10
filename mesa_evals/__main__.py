# MESA v0.3.0 — Package-level entrypoint for `python -m mesa_evals`
"""Delegates to the generator CLI when the package is invoked directly."""

from mesa_evals.generator import main

if __name__ == "__main__":
    main()
