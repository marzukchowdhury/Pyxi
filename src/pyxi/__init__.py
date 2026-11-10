"""Pyxi: a playful desktop AI buddy with a 3D explanation layer."""

import os as _os

# NumPy's OpenBLAS sets aside about 30 MB for every CPU core when it loads (490 MB on a 16-core
# PC), and Pyxi does no big matrix maths with it: speech and OCR run on their own engines.
# Must be set before NumPy is first imported.
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

__version__ = "0.1.11"
