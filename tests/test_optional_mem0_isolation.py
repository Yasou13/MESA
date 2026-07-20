import importlib


def test_benchmark_factory_import_has_no_mem0_side_effect(monkeypatch):
    real_import = importlib.import_module

    def guarded_import(name, package=None):
        if name == "mem0":
            raise AssertionError("Mem0 must not load during core factory import")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", guarded_import)
    import mesa_evals.benchmark_adapters.factory as factory

    assert callable(factory.get_adapter)
