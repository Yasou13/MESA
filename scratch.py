import pytest

if __name__ == "__main__":
    pytest.main(["tests/test_p0c_loop.py::test_consolidation_loop_full", "-W", "error"])
