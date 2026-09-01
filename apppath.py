"""Ruta base de la app: junto al .exe si está empaquetada con
PyInstaller, junto al código fuente en modo desarrollo."""
import os
import sys


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))
