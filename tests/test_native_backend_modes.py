import pytest
from core.ai.local_runtime import LocalLlamaRuntime

@pytest.mark.parametrize("backend", ["auto", "metal", "cpu"])
def test_backend_configuration(backend):
    runtime = LocalLlamaRuntime(backend=backend)
    assert runtime.backend == backend
    assert runtime.selected_backend == "unloaded"
    assert not runtime.is_loaded

def test_invalid_backend():
    with pytest.raises(ValueError):
        LocalLlamaRuntime(backend="invalid")

def test_default_backend():
    runtime = LocalLlamaRuntime()
    assert runtime.backend == "auto"

def test_initial_state():
    runtime = LocalLlamaRuntime(backend="cpu")
    assert runtime.is_loaded is False
    assert runtime.selected_backend == "unloaded"
