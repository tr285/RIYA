from core.agent.agent import RiyaAgent
from core.ai.local_runtime import LocalLlamaRuntime


MODEL_PATH = (
    "~/.cache/huggingface/hub/models--lmstudio-community--"
    "Qwen3-4B-Instruct-2507-GGUF/blobs/"
    "8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc"
)


def main():
    agent = RiyaAgent()

    ai = LocalLlamaRuntime()
    ai.load_model(MODEL_PATH)

    try:
        result = agent.decide_and_execute(
            "Set the volume to 40 percent",
            ai,
        )

        print("\nRIYA RESULT")
        print("-----------")
        print(result)

    finally:
        ai.unload_model()


if __name__ == "__main__":
    main()