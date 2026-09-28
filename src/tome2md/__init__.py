"""tome2md — turn books (PDF, EPUB, DOCX, MOBI, AZW3, TXT, ...) into a
chapter-split, Obsidian-ready Markdown vault, entirely on your own machine.
"""

from .converter import __version__, main

__all__ = ["__version__", "main"]
