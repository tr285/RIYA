from abc import ABC, abstractmethod


class AIRuntime(ABC):
    """
    Base interface for RIYA's local AI inference runtimes.

    RIYA Core communicates with this interface instead of
    depending directly on a specific AI engine.
    """

    @abstractmethod
    def load_model(self, model_path: str) -> None:
        """Load a local AI model."""
        raise NotImplementedError

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a response from the loaded model."""
        raise NotImplementedError

    @abstractmethod
    def unload_model(self) -> None:
        """Release the loaded model and its resources."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Return whether a model is currently loaded."""
        raise NotImplementedError