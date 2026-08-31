"""Qt desktop interface for StanThrust.

The package is layered so that presentation stays separate from the solvers:

- :mod:`stanthrust.ui.formatting` turns solver values into display text.
- :mod:`stanthrust.ui.widgets` holds the reusable cards, chips, and plot frames.
- :mod:`stanthrust.ui.inputs_panel` declares every design input as data.
- :mod:`stanthrust.ui.schematic` and :mod:`stanthrust.ui.model3d` draw the
  solved geometry.
- :mod:`stanthrust.ui.plots_panel`, :mod:`stanthrust.ui.data_panel`, and
  :mod:`stanthrust.ui.report` build the result views.
- :mod:`stanthrust.ui.main_window` wires those pieces to the solve pipeline.
"""

from stanthrust.ui.main_window import MainWindow, run

__all__ = ["MainWindow", "run"]
