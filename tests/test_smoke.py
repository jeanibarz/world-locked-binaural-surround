"""Smoke test: the package is importable and its toolchain is wired up.

This is the trivial green test B0.1 establishes so later blocks have a working
`pytest` harness to extend. Real engine/tracker tests arrive with M1+.
"""

import binaural_headtracker


def test_package_imports_and_has_version():
    assert isinstance(binaural_headtracker.__version__, str)
    assert binaural_headtracker.__version__.count(".") == 2  # semantic-ish x.y.z


def test_numpy_scipy_available():
    """The baseline numerics stack the render-engine prototype depends on."""
    import numpy as np
    import scipy.signal

    # A one-line sanity convolution — the operation at the heart of M1's engine.
    y = scipy.signal.fftconvolve(np.array([1.0, 0.0, 0.0]), np.array([1.0, 2.0]))
    assert np.allclose(y, [1.0, 2.0, 0.0, 0.0])
