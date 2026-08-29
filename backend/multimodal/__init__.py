"""
backend/multimodal/__init__.py
------------------------------
Multimodal (vision) support package for Phase 5.

Exports:
  ImageProcessor  — image validation, encoding, storage
  MultimodalService — orchestrates LLaVA vision + agent integration
"""

from backend.multimodal.image_processor import ImageProcessor
from backend.multimodal.service import MultimodalService

__all__ = ["ImageProcessor", "MultimodalService"]
