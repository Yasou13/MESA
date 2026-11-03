"""Performance and linear-time regression tests for legal_rules extractor (F-36)."""

import signal
import time
from pathlib import Path

import pytest

from mesa_memory.document_chunking import chunk_legal_document
from mesa_storage.legal_rules import extract

REPRO_CHUNK = (
    "5/6/2008 tarihli ve\n"
    "E.: 2008/16,\n"
    "K.: 2008/116 sayılı Kararı\n"
    "5982\n"
    "10, 20, 23, 41, 51, 53, 54, 74, 84, 94,\n"
    "125, 128, 129, 144, 145, 146, 147,148,\n"
    "149, 156, 157, 159, 166, Geçici Madde\n"
    "15, Geçici Madde 18, Geçici Madde\n"
    "19, İşlenemeyen Hüküm\n"
    "Bu Kanun 12/9/2010 tarihinde yapılan\n"
    "halkoylaması sonucu kabul edilmiş ve\n"
    "buna ilişkin Yüksek Seçim Kurulu Kararı\n"
    "23/9/2010 tarihli ve 27708 sayılı Resmî\n"
    "Gazete’de yayımlanmıştır."
)


def test_repro_chunk_under_50ms():
    """1. The repro chunk extracts in under 50 ms."""
    def _timeout_handler(signum, frame):
        raise TimeoutError("Extraction timed out (catastrophic backtracking)")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(1)
    try:
        t0 = time.perf_counter()
        extract(REPRO_CHUNK)
        elapsed = time.perf_counter() - t0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    assert elapsed < 0.05, f"Extraction took {elapsed * 1000:.1f}ms, expected < 50ms"


def test_tier_m_corpus_chunks_under_100ms():
    """2. Every chunk of Tier M corpus extracts in under 100 ms."""
    corpus_dir = Path("/home/yasin/Desktop/mesa_bench/v1/corpus")
    if not corpus_dir.is_dir():
        pytest.skip("bench folder absent")

    def _timeout_handler(signum, frame):
        raise TimeoutError("Chunk extraction timed out (>100ms)")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    try:
        for doc_path in sorted(corpus_dir.glob("*.txt")):
            text = doc_path.read_text(encoding="utf-8")
            chunks = chunk_legal_document(text)
            for i, chunk in enumerate(chunks):
                signal.alarm(1)
                t0 = time.perf_counter()
                try:
                    extract(chunk)
                    elapsed = time.perf_counter() - t0
                finally:
                    signal.alarm(0)
                assert elapsed < 0.1, f"Chunk {i} of {doc_path.name} took {elapsed * 1000:.1f}ms"
    finally:
        signal.signal(signal.SIGALRM, old_handler)


def test_adversarial_string_under_100ms():
    """3. A 20,000-character adversarial string extracts in under 100 ms."""
    pattern = "1, 2 ve 3 üncü maddelerinin ve 4, 5 ve 6 ncı maddeleri uyarınca "
    adversarial = (pattern * (20000 // len(pattern) + 1))[:20000]

    def _timeout_handler(signum, frame):
        raise TimeoutError("Adversarial extraction timed out")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(2)
    try:
        t0 = time.perf_counter()
        extract(adversarial)
        elapsed = time.perf_counter() - t0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    assert elapsed < 0.1, f"Adversarial extraction took {elapsed * 1000:.1f}ms, expected < 100ms"
